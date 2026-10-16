"""The journal (POST /api/worker/journal) and the job (GET /api/worker/job): binding to the first
instance, strict seq order, consumed invalid records, and the effects committing with
`journal_seq`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from agentos.models import (
    JobPackage,
    JournalRecord,
    PushedData,
    ResultData,
    RunEvent,
    StartedData,
    SyncBackData,
    SyncData,
    TicketDetail,
)
from agentos.worker_api import service as worker_service
from tests.run_support import CSRF, RunDriver, Worker, bearer, event, log_event, started_data

CreateTicket = Callable[..., TicketDetail]
FIXTURE = Path(__file__).parent / "fixtures" / "journal_v2.jsonl"


def error(r: Any) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def stranger(worker: Worker) -> Worker:
    """Another instance holding the same job token."""
    return Worker(worker.client, worker.run_id, worker.token, worker.manifest, seq=worker.seq)


def events(runs: RunDriver, run_id: int) -> list[tuple[int, str]]:
    return [(e["seq"], e["type"]) for e in runs.client.get(f"/api/runs/{run_id}/events").json()]


def journal_seq(runs: RunDriver, run_id: int) -> int:
    seq: int = runs.db_run(run_id)["journal_seq"]
    return seq


@pytest.fixture
def key(authed: TestClient, create_ticket: CreateTicket) -> str:
    return create_ticket("Journal").key


# ------------------------------------------------------------------ binding


def test_the_first_started_binds_the_job_to_its_instance(key: str, runs: RunDriver) -> None:
    run = runs.queue(key)
    runs.tick()
    token = runs.launcher.token(run.id)
    assert error(runs.client.get("/api/worker/job", headers=bearer("nope"))) == (
        401,
        "unauthorized",
    )

    worker = runs.bind(run.id, token)
    row = runs.db_run(run.id)
    assert (row["status"], row["journal_seq"]) == ("running", 1)
    assert row["started_at"] is not None
    assert worker.client.get("/api/worker/job", headers=worker.headers).status_code == 200

    other = stranger(worker)
    assert error(other.events(log_event(other.next_seq(), "plan", "mine"))) == (
        409,
        "already_claimed",
    )
    assert error(runs.client.get("/api/worker/job", headers=bearer(token))) == (
        409,
        "already_claimed",
    )
    assert error(other.sync_back(b"zip")) == (409, "already_claimed")
    assert events(runs, run.id) == []


def test_an_unbound_job_takes_only_a_valid_started_first(key: str, runs: RunDriver) -> None:
    run = runs.queue(key)
    runs.tick()
    token = runs.launcher.token(run.id)
    package = runs.client.get("/api/worker/job", headers=bearer(token)).json()
    worker = Worker(runs.client, run.id, token, JobPackage.model_validate(package))
    assert error(worker.events(log_event(1, "plan", "early"))) == (422, "validation_error")

    other = started_data() | {"protocol": 3}
    assert error(worker.journal(event(1, "started", None, **other))) == (422, "validation_error")
    assert runs.db_run(run.id)["status"] == "dispatched"  # nothing bound

    differs = started_data() | {"worker_sha256": "0" * 64}
    reply = worker.journal(event(1, "started", None, **differs))
    assert reply.json() == {"ack": 1, "cancel": False, "rejected": []}
    assert events(runs, run.id) == [(1, "warning")]  # in the started record's slot
    assert runs.db_run(run.id)["status"] == "running"


def test_a_started_of_another_job_binds_nothing(key: str, runs: RunDriver) -> None:
    run = runs.queue(key)
    runs.tick()
    token = runs.launcher.token(run.id)
    package = runs.client.get("/api/worker/job", headers=bearer(token)).json()
    worker = Worker(runs.client, run.id, token, JobPackage.model_validate(package))
    foreign = event(1, "started", None, **started_data()) | {"job": "1-1-0000000000000000"}
    assert error(worker.journal(foreign)) == (422, "validation_error")
    assert runs.db_run(run.id)["status"] == "dispatched"

    other = stranger(worker)
    assert other.journal(event(1, "started", None, **started_data())).json()["ack"] == 1
    assert runs.db_run(run.id)["status"] == "running"


def test_the_end_answers_410_except_a_repeat_of_the_final_result(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    worker.run_stage("plan")
    result = worker.record("result", status="completed")
    assert worker.journal(result).status_code == 200
    assert worker.journal(result).json()["ack"] == worker.seq  # a resent batch

    assert worker.complete().status_code == 200  # the same final result, a new seq
    assert error(worker.complete("failed")) == (410, "run_gone")
    assert error(worker.events(log_event(worker.next_seq(), "plan", "late"))) == (410, "run_gone")
    assert error(worker.events()) == (410, "run_gone")
    assert error(runs.client.get("/api/worker/job", headers=worker.headers)) == (410, "run_gone")
    assert runs.get(worker.run_id).status == "gated"


# ------------------------------------------------------------------ order


def test_a_duplicate_batch_changes_nothing(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    batch = [event(2, "stage_started", "plan"), log_event(3, "plan", "hi")]
    assert worker.events(*batch).json()["ack"] == 3
    before = (runs.get(worker.run_id), events(runs, worker.run_id))

    assert worker.events(*batch).json() == {"ack": 3, "cancel": False, "rejected": []}
    assert (runs.get(worker.run_id), events(runs, worker.run_id)) == before


def test_a_gap_stops_the_batch_and_ack_says_where_to_resend_from(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    reply = worker.events(log_event(2, "plan", "a"), log_event(4, "plan", "c"))
    assert reply.json()["ack"] == 2
    assert worker.events(log_event(4, "plan", "c")).json()["ack"] == 2  # a batch after a gap
    assert events(runs, worker.run_id) == [(2, "log")]

    resent = worker.events(log_event(3, "plan", "b"), log_event(4, "plan", "c"))
    assert resent.json()["ack"] == 4
    assert events(runs, worker.run_id) == [(2, "log"), (3, "log"), (4, "log")]


def test_an_invalid_record_is_consumed_with_a_warning_at_its_seq(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    reply = worker.journal(
        event(2, "log", "plan", stream="sideways", text="x"),
        event(3, "mystery", "plan"),
        event(4, "pushed", None, branch="b", head="0" * 40, commits=1, method="git")
        | {"job": "1-1-0000000000000000"},
        log_event(5, "plan", "fine"),
    )
    assert reply.json() == {"ack": 5, "cancel": False, "rejected": [2, 3, 4]}
    assert events(runs, worker.run_id) == [
        (2, "warning"),
        (3, "warning"),
        (4, "warning"),
        (5, "log"),
    ]
    assert runs.db_run(worker.run_id)["head_commit"] is None


def test_a_batch_that_fails_midway_commits_nothing_and_its_resend_applies_once(
    key: str, runs: RunDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = runs.start(key)
    agents = [{"path": "AGENTS.md", "content": "edited", "mode": "block"}]
    batch = [log_event(2, "plan", "before"), event(3, "sync", "plan", files=agents)]

    def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(worker_service, "write_version", broken)
    with pytest.raises(RuntimeError):
        worker.events(*batch)
    assert (journal_seq(runs, worker.run_id), events(runs, worker.run_id)) == (1, [])

    monkeypatch.undo()
    assert worker.events(*batch).json()["ack"] == 3
    assert worker.events(*batch).json()["ack"] == 3
    assert events(runs, worker.run_id) == [(2, "log")]
    versions = runs.client.get(f"/api/tickets/{key}/versions/AGENTS.md").json()
    assert [v["version"] for v in versions] == [1, 2]


# ------------------------------------------------------------------ effects


def test_a_sync_with_an_undeclared_path_applies_the_rest_and_is_rejected(
    key: str, runs: RunDriver
) -> None:
    worker = runs.start(key)
    files = [
        {"path": "src/app.py", "content": "x", "mode": "replace"},
        {"path": "AGENTS.md", "content": "edited", "mode": "block"},
    ]
    assert worker.sync("plan", files).json()["rejected"] == [2]
    [warning] = runs.client.get(f"/api/runs/{worker.run_id}/events").json()
    assert warning["seq"] == 2
    assert "src/app.py" in warning["data"]["message"]
    versions = runs.client.get(f"/api/tickets/{key}/versions/AGENTS.md").json()
    assert len(versions) == 2


def test_a_sync_back_record_must_match_the_upload(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    assert worker.sync_back(b"the zip", files=2).status_code == 204
    assert runs.get(worker.run_id).sync_back is not None

    digest = hashlib.sha256(b"the zip").hexdigest()
    wrong = worker.record("sync_back", sha256=digest, bytes=7, files=3)
    assert worker.journal(wrong).json()["rejected"] == [worker.seq]
    assert runs.get(worker.run_id).sync_back is None  # the unverified zip is dropped

    missing = worker.record("sync_back", sha256=digest, bytes=7, files=2)
    assert worker.journal(missing).json()["rejected"] == [worker.seq]


def test_the_upload_checks_token_and_instance_before_the_body(
    key: str, runs: RunDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = runs.start(key)
    monkeypatch.setattr(worker_service, "MAX_SYNC_BACK_BYTES", 8)
    worker.instance = "another"
    assert error(worker.sync_back(b"x" * 9)) == (409, "already_claimed")
    worker.token += "-wrong"
    assert error(worker.sync_back(b"x" * 9)) == (401, "unauthorized")


def test_a_pushed_record_stores_the_head(key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    head = "ab" * 20
    pushed = worker.record("pushed", branch=f"agent/{key}", head=head, commits=1, method="api")
    assert worker.journal(pushed).json()["rejected"] == []
    assert runs.db_run(worker.run_id)["head_commit"] == head
    assert events(runs, worker.run_id) == []  # not an event


def test_the_reply_carries_a_cancel(authed: TestClient, key: str, runs: RunDriver) -> None:
    worker = runs.start(key)
    assert authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).status_code == 200
    assert worker.events().json()["cancel"] is True


# ------------------------------------------------------------------ the fixture


def fixture_records() -> list[dict[str, Any]]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


DATA_MODELS: dict[str, Any] = {
    "started": StartedData,
    "sync": SyncData,
    "sync_back": SyncBackData,
    "pushed": PushedData,
    "result": ResultData,
}


def test_the_fixture_holds_every_record_type_and_each_validates() -> None:
    records = [JournalRecord.model_validate(r) for r in fixture_records()]
    assert [r.seq for r in records] == list(range(1, len(records) + 1))
    display = {"stage_started", "stage_finished", "log", "warning", "verdict", "verdict_repair"}
    assert {r.type for r in records} == display | set(DATA_MODELS)
    for r in records:
        if r.type in display:
            TypeAdapter(RunEvent).validate_python(r.model_dump(exclude={"job"}))
        else:
            DATA_MODELS[r.type].model_validate(r.data)


def test_the_fixture_ingests_whole(key: str, runs: RunDriver) -> None:
    run = runs.queue(key, "implement")
    runs.tick()
    token = runs.launcher.token(run.id)
    headers = bearer(token) | {"X-AgentOS-Instance": "fixture"}
    job = runs.client.get("/api/worker/job", headers=headers).json()["job"]["id"]
    records = [r | {"job": job} for r in fixture_records()]

    def post(batch: list[dict[str, Any]]) -> Any:
        return runs.client.post("/api/worker/journal", json={"records": batch}, headers=headers)

    # The fixture's sync_back record describes this upload, which follows `started`.
    assert post(records[:10]).json() == {"ack": 10, "cancel": False, "rejected": []}
    upload = runs.client.put(
        f"/api/worker/jobs/{job}/sync-back?sha256={hashlib.sha256(b'zip').hexdigest()}&files=1",
        content=b"zip",
        headers=headers,
    )
    assert upload.status_code == 204
    assert post(records).json() == {"ack": len(records), "cancel": False, "rejected": []}

    detail = runs.get(run.id)
    assert detail.status == "succeeded"
    assert [(s.stage_id, s.status, s.verdict) for s in detail.stage_runs] == [
        ("implement", "succeeded", None),
        ("review", "succeeded", "pass"),
        ("open-pr", "cancelled", None),
    ]
    assert detail.sync_back is not None
    row = runs.db_run(run.id)
    assert (row["journal_seq"], row["head_commit"]) == (13, "0123456789abcdef" * 2 + "01234567")
    stored = [seq for seq, _ in events(runs, run.id)]
    assert stored == [1, 2, 3, 4, 6, 7, 8, 9, 10]  # display rows, and started's sha warning
