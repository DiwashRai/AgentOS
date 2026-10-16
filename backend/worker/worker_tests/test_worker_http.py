"""HTTP client retry/backoff, the journal Reporter's error mapping, and the record sender (seq,
batching, heartbeat, per-run cap)."""

from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any

import pytest

import worker
from worker_tests.worker_support import JOURNAL, FakeServer, error, eventually, make_package


def _reporter(server: FakeServer) -> worker.HttpReporter:
    server.manifest = make_package([])
    return worker.HttpReporter(worker.Client(server.url, "j" * 43, "i" * 43))


def _sink(server: FakeServer) -> worker.EventSink:
    reporter: worker.Reporter = _reporter(server)
    return worker.EventSink(reporter, server.manifest["job"]["id"], 1)


def test_retries_5xx_then_succeeds(server: FakeServer) -> None:
    server.manifest = make_package([])
    answers = [error(503, "internal_error"), error(502, "upstream_error")]
    server.hook = lambda path, body: answers.pop(0) if answers else None
    client = worker.Client(server.url, "t" * 43, "i" * 43)
    assert client.post(JOURNAL, {"records": []}) == {"ack": 0, "cancel": False, "rejected": []}
    assert len(server.requests) == 3
    assert {(r.auth, r.instance) for r in server.requests} == {("Bearer " + "t" * 43, "i" * 43)}


def test_4xx_is_not_retried(server: FakeServer) -> None:
    server.hook = lambda path, body: error(422, "validation_error")
    with pytest.raises(worker.HttpError) as info:
        worker.Client(server.url, "t" * 43).post(JOURNAL, {})
    assert (info.value.status, info.value.code) == (422, "validation_error")
    assert len(server.requests) == 1


def test_unreachable_after_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "RETRY_BUDGET_S", 0.3)
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    started = time.monotonic()
    with pytest.raises(worker.Unreachable):
        worker.Client(f"http://127.0.0.1:{port}", "t" * 43).post(JOURNAL, {})
    assert 0.25 <= time.monotonic() - started < 5


def test_5xx_forever_is_unreachable(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "RETRY_BUDGET_S", 0.3)
    server.hook = lambda path, body: error(500, "internal_error")
    with pytest.raises(worker.Unreachable):
        worker.Client(server.url, "t" * 43).post(JOURNAL, {})
    assert len(server.requests) > 2


@pytest.mark.parametrize(
    ("status", "raised"),
    [
        (401, worker.RunGone),
        (410, worker.RunGone),
        (409, worker.AlreadyClaimed),
        (422, worker.Unreachable),
        (404, worker.Unreachable),
    ],
)
def test_the_reporter_maps_4xx(server: FakeServer, status: int, raised: type[Exception]) -> None:
    reporter = _reporter(server)
    server.hook = lambda path, body: error(status, "run_gone")
    with pytest.raises(raised):
        reporter.send([])
    assert len(server.requests) == 1


def test_records_have_monotonic_seq_and_batches_of_at_most_500(server: FakeServer) -> None:
    sink = _sink(server)
    sink.start()
    for i in range(1234):
        sink.log("build", "stdout", f"line {i}")
    sink.emit("stage_finished", "build", {"status": "succeeded", "exit_code": 0, "reason": None})
    sink.close()
    batches = [body["records"] for body in server.bodies(JOURNAL)]
    assert all(len(b) <= worker.MAX_EVENTS_PER_POST for b in batches)
    records = [r for b in batches for r in b]
    assert [r["seq"] for r in records] == list(range(1, 1236))
    assert records[0] == {
        "seq": 1,
        "ts": records[0]["ts"],
        "job": server.manifest["job"]["id"],
        "type": "log",
        "stage_id": "build",
        "data": {"stream": "stdout", "text": "line 0", "truncated": False},
    }
    assert datetime.fromisoformat(records[0]["ts"]).tzinfo is not None
    assert records[-1]["type"] == "stage_finished"


def test_a_failed_post_is_retried_with_the_same_seqs(server: FakeServer) -> None:
    answers = [error(503, "internal_error")]
    server.hook = lambda path, body: answers.pop(0) if answers and body["records"] else None
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "hello")
    sink.close()
    posted = [[r["seq"] for r in body["records"]] for body in server.bodies(JOURNAL)]
    assert [1] in posted
    assert posted.count([1]) == 2  # the 503'd batch went out again, unchanged


def test_empty_heartbeat_posts(server: FakeServer) -> None:
    sink = _sink(server)
    sink.start()
    try:
        eventually(
            lambda: sum(b["records"] == [] for b in server.bodies(JOURNAL)) >= 2,
            "two empty heartbeat posts",
        )
    finally:
        sink.close()


def test_cancel_flag_from_the_server(server: FakeServer) -> None:
    server.cancel = True
    sink = _sink(server)
    sink.start()
    sink.log(None, "system", "x")
    assert sink.cancel.wait(2)
    sink.close()


def test_run_gone_stops_the_sender(server: FakeServer) -> None:
    sink = _sink(server)
    server.hook = lambda path, body: error(410, "run_gone")
    sink.start()
    sink.log(None, "system", "x")
    with pytest.raises(worker.RunGone):
        sink.close()
    assert isinstance(sink.failure, worker.RunGone)


def test_another_4xx_stops_the_sender_as_unreachable(server: FakeServer) -> None:
    """A dropped batch would leave a gap the server waits at forever."""
    sink = _sink(server)
    server.hook = lambda path, body: error(422, "validation_error") if body["records"] else None
    sink.start()
    sink.log(None, "system", "bad")
    with pytest.raises(worker.Unreachable):
        sink.close()
    assert server.records() == []


def test_log_records_beyond_the_run_cap_are_dropped_with_one_warning(
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
    records: list[dict[str, Any]] = server.records()
    types = [r["type"] for r in records]
    assert types.count("log") == 49
    assert types.count("warning") == 1
    assert types[-1] == "stage_finished"
    assert len(records) <= 60
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
