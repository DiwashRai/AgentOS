"""HTTP client retry/backoff, worker-token error mapping, and the event flusher (seq, batching,
heartbeat, per-run cap)."""

from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any

import pytest

import worker
from worker_tests.worker_support import FakeServer, error


def _sink(server: FakeServer) -> worker.EventSink:
    return worker.EventSink(worker.WorkerApi(worker.Client(server.url, "w" * 43)))


def test_retries_5xx_then_succeeds(server: FakeServer) -> None:
    answers = [error(503, "internal_error"), error(502, "upstream_error")]
    server.hook = lambda path, body: answers.pop(0) if answers else None
    client = worker.Client(server.url, "t" * 43)
    assert client.post("/api/worker/events", {"events": []}) == {"cancel": False}
    assert len(server.requests) == 3
    assert {r.auth for r in server.requests} == {"Bearer " + "t" * 43}


def test_4xx_is_not_retried(server: FakeServer) -> None:
    server.hook = lambda path, body: error(422, "validation_error")
    with pytest.raises(worker.HttpError) as info:
        worker.Client(server.url, "t" * 43).post("/api/worker/sync", {})
    assert (info.value.status, info.value.code) == (422, "validation_error")
    assert len(server.requests) == 1


def test_unreachable_after_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "RETRY_BUDGET_S", 0.3)
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    started = time.monotonic()
    with pytest.raises(worker.Unreachable):
        worker.Client(f"http://127.0.0.1:{port}", "t" * 43).post("/api/worker/events", {})
    assert 0.25 <= time.monotonic() - started < 5


def test_5xx_forever_is_unreachable(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "RETRY_BUDGET_S", 0.3)
    server.hook = lambda path, body: error(500, "internal_error")
    with pytest.raises(worker.Unreachable):
        worker.Client(server.url, "t" * 43).post("/api/worker/events", {})
    assert len(server.requests) > 2


@pytest.mark.parametrize("status", [401, 410])
def test_worker_api_maps_401_and_410_to_run_gone(server: FakeServer, status: int) -> None:
    server.hook = lambda path, body: error(status, "run_gone")
    with pytest.raises(worker.RunGone):
        worker.WorkerApi(worker.Client(server.url, "w" * 43)).call("/api/worker/sync", {})


def test_events_have_monotonic_seq_and_batches_of_at_most_500(server: FakeServer) -> None:
    sink = _sink(server)
    sink.start()
    for i in range(1234):
        sink.log("build", "stdout", f"line {i}")
    sink.emit("stage_finished", "build", {"status": "succeeded", "exit_code": 0, "reason": None})
    sink.close()
    batches = [body["events"] for body in server.bodies("/api/worker/events")]
    assert all(len(b) <= worker.MAX_EVENTS_PER_POST for b in batches)
    events = [e for b in batches for e in b]
    assert [e["seq"] for e in events] == list(range(1, 1236))
    assert events[0] == {
        "seq": 1,
        "ts": events[0]["ts"],
        "stage_id": "build",
        "type": "log",
        "data": {"stream": "stdout", "text": "line 0", "truncated": False},
    }
    assert datetime.fromisoformat(events[0]["ts"]).tzinfo is not None
    assert events[-1]["type"] == "stage_finished"


def test_a_rejected_post_is_retried_with_the_same_seqs(server: FakeServer) -> None:
    answers = [error(503, "internal_error")]
    server.hook = lambda path, body: answers.pop(0) if answers and body["events"] else None
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "hello")
    sink.close()
    posted = [[e["seq"] for e in body["events"]] for body in server.bodies("/api/worker/events")]
    assert [1] in posted
    assert posted.count([1]) == 2  # the 503'd batch went out again, unchanged


def test_empty_heartbeat_posts(server: FakeServer) -> None:
    sink = _sink(server)
    sink.start()
    time.sleep(0.7)  # HEARTBEAT_S is 0.2 in tests
    sink.close()
    bodies = server.bodies("/api/worker/events")
    assert len([b for b in bodies if b["events"] == []]) >= 2


def test_cancel_flag_from_the_server(server: FakeServer) -> None:
    server.cancel = True
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "x")
    assert sink.cancel.wait(2)
    sink.close()


def test_run_gone_stops_the_flusher(server: FakeServer) -> None:
    server.hook = lambda path, body: error(410, "run_gone")
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "x")
    with pytest.raises(worker.RunGone):
        sink.close()
    assert isinstance(sink.failure, worker.RunGone)


def test_other_4xx_drops_the_batch_and_warns_once(server: FakeServer) -> None:
    answers = [error(422, "validation_error")]
    rejected: list[Any] = []

    def hook(path: str, body: Any) -> tuple[int, Any] | None:
        if answers and body["events"]:
            rejected.append(body)
            return answers.pop(0)
        return None

    server.hook = hook
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "bad")
    time.sleep(0.3)  # rejected, then the warning goes out in the next post
    answers.append(error(422, "validation_error"))
    sink.log(None, "system", "also dropped")
    time.sleep(0.3)
    sink.log(None, "system", "fine")
    sink.close()
    assert len(rejected) == 2
    accepted = [e for e in server.events() if all(e not in b["events"] for b in rejected)]
    texts = [e["data"].get("text") or e["data"].get("message") for e in accepted]
    assert "bad" not in texts
    assert "also dropped" not in texts
    assert texts.count("fine") == 1
    assert sum("events rejected" in str(t) for t in texts) == 1


def test_log_events_beyond_the_run_cap_are_dropped_with_one_warning(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "MAX_EVENTS_PER_RUN", 60)
    monkeypatch.setattr(worker, "EVENT_RESERVE", 10)
    sink = _sink(server)
    sink.start()
    sink.emit("stage_started", "s", {})
    for i in range(100):
        sink.log("s", "stdout", str(i))
    sink.emit("stage_finished", "s", {"status": "succeeded", "exit_code": 0, "reason": None})
    sink.close()
    events: list[dict[str, Any]] = server.events()
    types = [e["type"] for e in events]
    assert types.count("log") == 49
    assert types.count("warning") == 1
    assert types[-1] == "stage_finished"
    assert len(events) <= 60
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
