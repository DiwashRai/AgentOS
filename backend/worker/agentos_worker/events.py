"""The journal's sender: `EventSink` numbers records and sends them through a Reporter
from a background thread, keeping each until it is acked.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from agentos_worker import tunables
from agentos_worker.console import say
from agentos_worker.worker_api import Reporter, ResultRejected, RunGone, Unreachable

MAX_EVENTS_PER_POST = 500
MAX_EVENTS_PER_RUN = 100_000
EVENT_RESERVE = 1_000  # head-room kept for non-log events once log events are dropped


def now() -> str:
    return datetime.now(UTC).isoformat()


class EventSink:
    """Numbers journal records from `seq` on and sends them through a Reporter from a background
    thread (SPEC §9.1), keeping each until it is acked: every send starts at `ack + 1`.

    Batches go out every FLUSH_INTERVAL_S, as soon as FLUSH_THRESHOLD are buffered, and at least
    every HEARTBEAT_S even when empty. `cancel` is set when the server asks for it; `failure`
    holds RunGone/Unreachable once sending is impossible.
    """

    def __init__(self, reporter: Reporter, job: str, seq: int) -> None:
        self._reporter = reporter
        self._job = job
        self._cond = threading.Condition()
        self._buf: list[dict[str, Any]] = []  # every record not acked yet, in seq order
        self._seq = seq - 1  # the last seq given out
        self._logs_dropped = False
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="agentos-events", daemon=True)
        self.cancel = threading.Event()
        self.failure: RunGone | Unreachable | None = None

    def start(self) -> None:
        self._thread.start()

    def emit(self, type_: str, stage_id: str | None, data: dict[str, Any]) -> None:
        with self._cond:
            if type_ == "log" and self._seq >= MAX_EVENTS_PER_RUN - EVENT_RESERVE:
                if self._logs_dropped:
                    return
                self._logs_dropped = True
                type_ = "warning"
                data = {"message": "too many events in this run: further log output is dropped"}
            self._seq += 1
            record = {"seq": self._seq, "ts": now(), "job": self._job, "type": type_}
            self._buf.append({**record, "stage_id": stage_id, "data": data})
            if len(self._buf) >= tunables.FLUSH_THRESHOLD:
                self._cond.notify_all()

    def log(self, stage_id: str | None, stream: str, text: str, truncated: bool = False) -> None:
        self.emit("log", stage_id, {"stream": stream, "text": text, "truncated": truncated})

    def warn(self, stage_id: str | None, message: str) -> None:
        say(f"warning: {message}")
        self.emit("warning", stage_id, {"message": message})

    def check(self) -> None:
        """Raise RunGone/Unreachable if the event poster hit one."""
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        """Send what is buffered, stop the thread, close the Reporter, then raise any failure."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread.is_alive():
            self._thread.join()
        self._reporter.close()
        self.check()

    def abandon(self) -> None:
        """Stop the thread without sending what is buffered (the run is over without a result:
        taken away, rejected or unreachable). A no-op once `close` has run. A send in flight
        retries no more; its last attempt is waited out for one HTTP timeout at most."""
        with self._cond:
            self._buf = []  # nothing left to send: the loop stops at its next wake
            self._stopping = True
            self._cond.notify_all()
        self._reporter.stop()
        if self._thread.is_alive():
            self._thread.join(tunables.HTTP_TIMEOUT_S)

    def _loop(self) -> None:
        last_post = time.monotonic()
        while True:
            with self._cond:
                self._cond.wait_for(
                    lambda: self._stopping or len(self._buf) >= tunables.FLUSH_THRESHOLD,
                    timeout=tunables.FLUSH_INTERVAL_S,
                )
                batch = self._buf[:MAX_EVENTS_PER_POST]
                stopping = self._stopping
            if not batch and (stopping or time.monotonic() - last_post < tunables.HEARTBEAT_S):
                if stopping:
                    return
                continue
            try:
                ack = self._reporter.send(batch)
            except (RunGone, Unreachable) as exc:
                self.failure = exc
                return

            last_post = time.monotonic()
            if ack.cancel:
                self.cancel.set()
            if batch and ack.seq < batch[0]["seq"]:  # nothing applied: a resend would loop
                self.failure = Unreachable(f"record {batch[0]['seq']} is never acked ({ack})")
                return

            refused = [r for r in batch if r["type"] == "result" and r["seq"] in ack.rejected]
            if refused:
                self.failure = ResultRejected(f"the server rejected result {refused[0]['seq']}")
                return

            with self._cond:
                self._buf = [r for r in self._buf if r["seq"] > ack.seq]
