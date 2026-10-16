"""Mailbox mode: worker.main(--manifest, --outbox, --workdir) records its API calls to a file.

The outbox is what the server replays: a header line, then one line per call in call order, with
no heartbeats, ending in the complete call. Also the CLI's one-mode rule and the refusals.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import worker
from worker_tests.worker_support import (
    JOB_TOKEN,
    SKILL_DIR,
    agent_stage,
    make_package,
    python_shell,
    shell_stage,
)

EVENTS, SYNC, COMPLETE = "/api/worker/events", "/api/worker/sync", "/api/worker/complete"


def run_mailbox(tmp_path: Path, manifest: Any) -> tuple[int, Path]:
    """Run the worker in mailbox mode on `manifest` (a dict, or raw bytes for the file)."""
    manifest_path = tmp_path / "manifest.json"
    data = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
    manifest_path.write_bytes(data)
    outbox = tmp_path / "outbox.jsonl"
    code = worker.main(
        [
            "--manifest",
            str(manifest_path),
            "--outbox",
            str(outbox),
            "--workdir",
            str(tmp_path / "ws"),
        ]
    )
    return code, outbox


def lines(outbox: Path) -> list[dict[str, Any]]:
    raw = outbox.read_bytes()
    assert raw.isascii(), "json.dumps' default ensure_ascii, as Client.post sends"
    assert raw.endswith(b"\n")
    return [json.loads(line) for line in raw.decode().split("\n")[:-1]]


def test_no_repo_run_records_header_events_sync_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_shell(monkeypatch)
    manifest = make_package(
        [
            agent_stage("implement", "edit", "sleep=0.6"),  # > HEARTBEAT_S: heartbeats happen
            shell_stage("lint", "print('lint ok')"),
        ]
    )
    manifest["context"][2]["content"] = "# Tasks — ü\n\n- [ ] Fix expiry\n"
    code, outbox = run_mailbox(tmp_path, manifest)
    assert code == 0

    recorded = lines(outbox)
    assert outbox.read_bytes().startswith(b'{"outbox": 1, "run_id": 42}\n')
    assert recorded[0] == {"outbox": 1, "run_id": 42}
    calls = recorded[1:]
    assert all(set(c) == {"path", "body"} for c in calls)
    assert [c["path"] for c in calls].count(COMPLETE) == 1
    assert calls[-1] == {
        "path": COMPLETE,
        "body": {"status": "completed", "failed_stage": None, "error": None},
    }

    batches = [c["body"]["events"] for c in calls if c["path"] == EVENTS]
    assert batches
    assert all(batches), "empty batches (heartbeats) are never recorded"
    events = [e for batch in batches for e in batch]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    kinds = [(e["type"], e["stage_id"]) for e in events if e["type"].startswith("stage_")]
    assert kinds == [
        ("stage_started", "implement"),
        ("stage_finished", "implement"),
        ("stage_started", "lint"),
        ("stage_finished", "lint"),
    ]
    assert any(e["type"] == "log" and e["data"]["text"] == "lint ok" for e in events)

    syncs = [c["body"] for c in calls if c["path"] == SYNC]
    assert [s["stage_id"] for s in syncs] == ["implement", "lint"]
    first = {f["path"]: f["content"] for f in syncs[0]["files"]}
    assert first[f"{SKILL_DIR}/TASKS.md"] == "# Tasks — ü\n\n- [x] Fix expiry\n"
    assert first[f"{SKILL_DIR}/learnings.log"] == "expiry is computed in minutes\n"
    assert not (tmp_path / "ws").exists(), "the workdir is deleted on completion"


def test_a_failed_stage_records_a_failed_complete(tmp_path: Path) -> None:
    code, outbox = run_mailbox(tmp_path, make_package([agent_stage("implement", "exit=3")]))
    assert code == 0
    assert lines(outbox)[-1] == {
        "path": COMPLETE,
        "body": {"status": "failed", "failed_stage": "implement", "error": "exited with code 3"},
    }


@pytest.mark.parametrize(
    "extra",
    [
        ["--server", "http://127.0.0.1:8000"],
        ["--token", JOB_TOKEN],
    ],
)
def test_mailbox_mode_refuses_http_options(extra: list[str], tmp_path: Path) -> None:
    mailbox = ["--manifest", "m.json", "--outbox", "o.jsonl", "--workdir", str(tmp_path)]
    with pytest.raises(SystemExit):
        worker.parse_args([*mailbox, *extra])


@pytest.mark.parametrize(
    "argv",
    [
        ["--manifest", "m.json", "--outbox", "o.jsonl"],
        ["--manifest", "m.json", "--workdir", "ws"],
        ["--outbox", "o.jsonl", "--workdir", "ws"],
        ["--workdir", "ws"],
    ],
)
def test_each_mode_needs_its_options(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        worker.parse_args(argv)


def test_mailbox_args(tmp_path: Path) -> None:
    args = worker.parse_args(["--manifest", "m.json", "--outbox", "o.jsonl", "--workdir", "ws"])
    assert args.mailbox
    assert not worker.parse_args(["--server", "http://127.0.0.1:8000", "--token", "t"]).mailbox


def test_an_existing_outbox_is_refused(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_bytes(b"earlier\n")
    code, _ = run_mailbox(tmp_path, make_package([agent_stage("implement", "print=hi")]))
    assert code == 1
    assert outbox.read_bytes() == b"earlier\n"
    assert not (tmp_path / "ws").exists(), "nothing ran"


@pytest.mark.parametrize(
    "manifest",
    [b"{not json", b"\xff\xfe", {"run_id": 42}, {**make_package([]), "protocol": 1}],
)
def test_a_bad_manifest_exits_1_without_an_outbox(tmp_path: Path, manifest: Any) -> None:
    code, outbox = run_mailbox(tmp_path, manifest)
    assert code == 1
    assert not outbox.exists()
    assert not (tmp_path / "ws").exists()


def test_a_missing_manifest_exits_1_without_an_outbox(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox.jsonl"
    argv = ["--manifest", str(tmp_path / "nope.json"), "--outbox", str(outbox)]
    assert worker.main([*argv, "--workdir", str(tmp_path / "ws")]) == 1
    assert not outbox.exists()


def test_an_outbox_write_error_aborts_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing write in the events thread becomes a visible failure: the stage is killed and
    the worker exits 1 without completing (no silently dead thread, no 30 s wait)."""
    real = worker.Recorder._write

    def write(fh: Any, line: dict[str, Any]) -> None:
        if line.get("path") == EVENTS:
            raise OSError(28, "No space left on device")
        real(fh, line)

    monkeypatch.setattr(worker.Recorder, "_write", staticmethod(write))
    started = time.monotonic()
    code, outbox = run_mailbox(tmp_path, make_package([agent_stage("implement", "sleep=30")]))
    assert code == 1
    assert time.monotonic() - started < 20
    assert lines(outbox) == [{"outbox": 1, "run_id": 42}], "no complete after the failure"


def record(seq: int, type_: str, data: dict[str, Any]) -> dict[str, Any]:
    """A journal record of stage `s`."""
    ts = "2026-10-16T12:00:00+00:00"
    return {"seq": seq, "ts": ts, "job": "7-1-x", "type": type_, "stage_id": "s", "data": data}


def test_a_closed_recorder_refuses_further_records(tmp_path: Path) -> None:
    recorder: worker.Reporter = worker.Recorder(tmp_path / "outbox.jsonl", 7)
    assert recorder.send([]) == worker.Ack(0, False, ())
    assert recorder.send([record(2, "sync", {"files": []})]) == worker.Ack(2, False, ())
    recorder.close()
    result = {"status": "completed", "failed_stage": None, "error": None, "pr_url": None}
    with pytest.raises(worker.Unreachable):
        recorder.send([record(3, "result", result)])
    assert lines(tmp_path / "outbox.jsonl") == [
        {"outbox": 1, "run_id": 7},
        {"path": SYNC, "body": {"stage_id": "s", "files": []}},
    ]
