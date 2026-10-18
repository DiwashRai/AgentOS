"""The Git Data API of one repo (`GitHub`): one HTTP call, retried within a budget, and its
failure as a GitHubError. A GitHub failure is a stage failure, never a lost run or an
unreachable server. `publishing` sequences these calls into one published commit.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import IO, Any

from agentos_worker import tunables
from agentos_worker.console import say
from agentos_worker.publication import classify, retry_wait, scrub

GITHUB_RETRY_BUDGET_S = 300.0  # rate limits, network errors and 5xx are retried this long


GITHUB_API_VERSION = "2022-11-28"


class GitHubError(Exception):
    """A GitHub call failed for good. `action` is classify()'s answer; the message is GitHub's,
    scrubbed and clipped. Never a RunGone or Unreachable: it only ever fails the stage."""

    def __init__(self, status: int | None, message: str, action: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status is not None else message)
        self.status = status
        self.message = message
        self.action = action


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would forward Authorization to any host it is redirected to: follow nothing."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class GitHub:
    """The Git Data API of one repo with the job's token. Separate from Client/WorkerApi on
    purpose: a GitHub failure is a stage failure, never a lost run or an unreachable server."""

    def __init__(self, api_url: str, token: str, repo: str) -> None:
        self.repo = repo
        self._base = f"{api_url.rstrip('/')}/repos/{repo}"
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect())

    def call(self, method: str, path: str, body: Any = None, *, ref_write: bool = False) -> Any:
        """Send one request under /repos/<owner>/<repo> and return the decoded answer. What
        classify() calls "retry" is retried within GITHUB_RETRY_BUDGET_S; anything else raises
        GitHubError."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        deadline = time.monotonic() + GITHUB_RETRY_BUDGET_S
        delay = tunables.BACKOFF_START_S
        while True:
            request = urllib.request.Request(  # noqa: S310 -- GITHUB_API_URL is checked to be https
                self._base + path,
                data=data,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "User-Agent": "agentos-worker",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
            )
            status: int | None = None
            wait: float | None = None
            try:
                with self._opener.open(request, timeout=tunables.HTTP_TIMEOUT_S) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as exc:
                status, message = exc.code, self._message(exc)
                headers = {k.lower(): v for k, v in exc.headers.items()}
                wait = retry_wait(headers, time.time())
            except (OSError, http.client.HTTPException, ValueError) as exc:
                message = f"{type(exc).__name__}: {exc}"
            message = scrub(message, (self._token,))

            action = classify(status, message, wait, ref_write)
            remaining = deadline - time.monotonic()
            pause = delay if wait is None else wait
            if action != "retry" or pause > remaining:
                raise GitHubError(status, message, action)
            say(f"GitHub {method} {path} failed ({message}); retrying in {pause:.1f}s")
            time.sleep(pause)
            delay = min(delay * 2, tunables.BACKOFF_CAP_S)

    @staticmethod
    def _message(exc: urllib.error.HTTPError) -> str:
        if 300 <= exc.code < 400:
            return "GitHub redirected the request: fix the ticket's repo URL"
        message = str(exc.reason)
        try:
            body = json.loads(exc.read() or b"null")
            if isinstance(body, dict) and isinstance(body.get("message"), str):
                message = body["message"]
        except (OSError, ValueError):
            pass
        finally:
            exc.close()
        return message
