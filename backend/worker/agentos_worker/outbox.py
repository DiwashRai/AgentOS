"""Mailbox mode's Reporter: `Recorder` writes the journal to the outbox file, open until
closed or a write fails.
"""

from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path
from typing import IO, Any

from agentos_worker.worker_api import Ack, Unreachable


class Recorder:
    """Mailbox mode: writes the journal to the outbox as the worker API calls the server replays.

    Line 1 is `{"outbox": 1, "run_id": N}`, then one flushed `{"path", "body"}` line per call:
    consecutive display records as one events call (events numbered 1..N), `sync` as a sync call,
    `result` as complete; nothing else is written. Every record is acked at once, never with a
    cancel. A failed write raises Unreachable (and every later send too), so the run aborts."""

    def __init__(self, outbox: Path, run_id: int) -> None:
        self._lock = threading.Lock()
        self._events = 0  # the last event seq written
        self._fh: IO[bytes] | None = outbox.open("xb")  # never overwrite an earlier recording
        self._closed = "the outbox is closed"
        try:
            self._write(self._fh, {"outbox": 1, "run_id": run_id})
        except OSError:
            self.close()
            raise

    @staticmethod
    def _write(fh: IO[bytes], line: dict[str, Any]) -> None:
        fh.write(json.dumps(line).encode("utf-8") + b"\n")
        fh.flush()

    def send(self, records: list[dict[str, Any]]) -> Ack:
        calls: list[dict[str, Any]] = []
        for record in records:
            kind, data = record["type"], record["data"]
            if kind == "sync":
                body = {"stage_id": record["stage_id"], **data}
                calls.append({"path": "/api/worker/sync", "body": body})
            elif kind == "result":
                body = {k: v for k, v in data.items() if k != "pr_url" or v is not None}
                calls.append({"path": "/api/worker/complete", "body": body})
            elif kind not in ("started", "pushed", "sync_back"):
                if not calls or calls[-1]["path"] != "/api/worker/events":
                    calls.append({"path": "/api/worker/events", "body": {"events": []}})
                self._events += 1
                event = {"seq": self._events, "ts": record["ts"], "stage_id": record["stage_id"]}
                calls[-1]["body"]["events"].append({**event, "type": kind, "data": data})

        with self._lock:
            for call in calls:
                if self._fh is None:
                    raise Unreachable(f"record {call['path']}: {self._closed}")
                try:
                    self._write(self._fh, call)
                except OSError as exc:
                    self._shut(f"cannot write the outbox: {exc}")
                    raise Unreachable(f"record {call['path']}: {self._closed}") from exc
        return Ack(records[-1]["seq"] if records else 0, False, ())

    def stop(self) -> None:
        """Nothing to do: a write is never retried."""

    def close(self) -> None:
        with self._lock:
            self._shut("the outbox is closed")

    def _shut(self, why: str) -> None:
        """Close the file; every later call raises Unreachable with `why`."""
        if self._fh is not None:
            fh, self._fh, self._closed = self._fh, None, why
            with contextlib.suppress(OSError):
                fh.close()
