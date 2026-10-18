"""The worker API over HTTP: `Client` (retries network errors and 5xx), the errors a call
ends in (HttpError, RunGone, AlreadyClaimed, ResultRejected, Unreachable), and the journal's
`Reporter` protocol with `HttpReporter`, live mode's.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from agentos_worker import tunables
from agentos_worker.console import say

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class HttpError(Exception):
    """A 4xx answer (not retried)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class RunGone(Exception):
    """401/410 from the journal: the run was taken away. Never write to the branch again."""


class AlreadyClaimed(RunGone):
    """409 from the journal: the job is bound to another worker instance."""


class ResultRejected(RunGone):
    """The server consumed the `result` record without applying it: the run didn't end, so the
    job failed to complete. Like a gone run: nothing more is published, the workdir is kept."""


class Unreachable(Exception):
    """Network errors or 5xx for RETRY_BUDGET_S (or the journal can't be written any more)."""


def _http_error(exc: urllib.error.HTTPError) -> HttpError:
    code, message = "http_error", str(exc.reason)
    try:
        body = json.loads(exc.read() or b"null")
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = str(body["error"].get("code", code))
            message = str(body["error"].get("message", message))
    except (OSError, ValueError):
        pass
    finally:
        exc.close()
    return HttpError(exc.code, code, message)


class Client:
    """JSON requests and raw PUTs with a bearer token, plus the worker instance's id when given.
    Retries network errors and 5xx with exponential backoff."""

    def __init__(self, server: str, token: str, instance: str = "") -> None:
        self._base = server.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        if instance:
            self._headers["X-AgentOS-Instance"] = instance
        host = urllib.parse.urlsplit(self._base).hostname or ""
        # A loopback server must never go through a configured proxy.
        handlers = [urllib.request.ProxyHandler({})] if host in _LOOPBACK else []
        self._opener = urllib.request.build_opener(*handlers)

    def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        timeout_s: float | None = None,
        retry_budget_s: float | None = None,
    ) -> Any:
        """POST `body` and return the decoded answer. `timeout_s` (default HTTP_TIMEOUT_S) bounds
        each attempt; failures are retried for `retry_budget_s` (default RETRY_BUDGET_S, 0 = one
        attempt), then raise Unreachable. A 4xx raises HttpError at once."""
        data = json.dumps(body).encode("utf-8")
        return self._send("POST", path, data, "application/json", timeout_s, retry_budget_s)

    def get(self, path: str) -> Any:
        """GET and return the decoded answer, retried like `post`."""
        return self._send("GET", path, None, "application/json", None, None)

    def put(self, path: str, data: bytes) -> Any:
        """PUT raw bytes (a zip), retried like `post`; each attempt gets UPLOAD_TIMEOUT_S."""
        return self._send("PUT", path, data, "application/zip", tunables.UPLOAD_TIMEOUT_S, None)

    def _send(
        self,
        method: str,
        path: str,
        data: bytes | None,
        content_type: str,
        timeout_s: float | None,
        retry_budget_s: float | None,
    ) -> Any:
        timeout = tunables.HTTP_TIMEOUT_S if timeout_s is None else timeout_s
        deadline = time.monotonic() + (
            tunables.RETRY_BUDGET_S if retry_budget_s is None else retry_budget_s
        )
        delay = tunables.BACKOFF_START_S
        while True:
            request = urllib.request.Request(  # noqa: S310 -- the scheme is checked in parse_args
                self._base + path,
                data=data,
                method=method,
                headers={**self._headers, "Content-Type": content_type},
            )
            try:
                with self._opener.open(request, timeout=timeout) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise _http_error(exc) from None
                exc.close()
                problem = f"HTTP {exc.code}"
            except (OSError, http.client.HTTPException, ValueError) as exc:
                problem = f"{type(exc).__name__}: {exc}"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unreachable(f"{method} {path}: {problem}")
            say(f"{method} {path} failed ({problem}); retrying in {delay:.1f}s")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, tunables.BACKOFF_CAP_S)


@dataclass(frozen=True)
class Ack:
    """A Reporter's answer: the highest seq the destination holds (records after it are sent
    again), whether a cancel was requested, and the seqs it consumed as invalid."""

    seq: int
    cancel: bool
    rejected: tuple[int, ...]


class Reporter(Protocol):
    """Where the journal goes: the server (HttpReporter) or the mailbox outbox (Recorder)."""

    def send(self, records: list[dict[str, Any]]) -> Ack:
        """Deliver records in seq order; [] is a heartbeat. Raises RunGone (AlreadyClaimed
        included) or Unreachable."""
        ...

    def close(self) -> None: ...


class HttpReporter:
    """Live mode: posts records to /api/worker/journal. 401/410 raise RunGone, 409
    AlreadyClaimed; any other 4xx stops reporting as Unreachable, since the server would wait
    forever at the gap a dropped batch leaves."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def send(self, records: list[dict[str, Any]]) -> Ack:
        try:
            resp = self._client.post("/api/worker/journal", {"records": records})
        except HttpError as exc:
            if exc.status == 409:
                raise AlreadyClaimed(str(exc)) from exc
            if exc.status in (401, 410):
                raise RunGone(str(exc)) from exc
            raise Unreachable(f"the journal was refused: {exc}") from exc
        return Ack(resp["ack"], resp["cancel"] is True, tuple(resp["rejected"]))

    def close(self) -> None:
        """Nothing to release: every post is a request of its own."""
