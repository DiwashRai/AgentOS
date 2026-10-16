"""GitHubActionsProvider: dispatches a workflow that runs `worker.py` on a runner.

The workflow (`backend/deploy/github/agentos-worker.yml`, installed as
`.github/workflows/agentos-worker.yml` on the target repo's default branch) masks the token,
downloads GET /api/worker/worker.py from `server_url` and runs it in its `work` job. GitHub
queues the job. Which repo/workflow/ref is asked of `target()` at every start (they are edited
in Settings > Runners); the token and API URL/version are fixed (env).

REST facts (docs.github.com, checked 2026-10-11):
- `POST /repos/{repo}/actions/workflows/{workflow}/dispatches` with `{ref, inputs}`.
- API version 2022-11-28 (the default here): the optional `return_run_details: true` (added
  2026-02-19) turns the 204 No Content into a 200 with `{workflow_run_id, run_url, html_url}`.
- API version 2026-03-10 removed that parameter and always answers 200 with the run details.
- GHES may predate both; a 204 is still handled (ref = the workflow's runs page, naming no run).
- Fine-grained PAT: repository permission "Actions: write" on the one repo.
- `GET .../actions/runs/{id}/jobs` lists the latest attempt's jobs (`name`, `status`,
  `conclusion`, `started_at`); `POST .../actions/runs/{id}/cancel` answers 202, or 409 once the
  run has finished.

Mailbox mode (`launchers/mailbox.py`) dispatches with its own inputs (`dispatch()`). `status` and
`cancel` read only the path of a ref, which must name a run of the current target repo; the
request always goes to the configured API URL.

Neither the GitHub token nor the job token ever appears in a raised message.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from agentos.launchers.base import JobStart, JobStatus, ProviderError
from agentos.models import GITHUB_REPO_PATTERN, GITHUB_WORKFLOW_PATTERN

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
# From this version on, run details are always returned and `return_run_details` is gone.
_RUN_DETAILS_BUILT_IN = "2026-03-10"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_MAX_MESSAGE_CHARS = 300
# The job of both workflows that runs the worker (its key; neither gives it a `name:`).
WORK_JOB = "work"
_WAITING = frozenset({"queued", "waiting", "pending", "requested"})

_REPO = re.compile(GITHUB_REPO_PATTERN)
_WORKFLOW = re.compile(GITHUB_WORKFLOW_PATTERN)
_API_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The path of an Actions run's html_url (github.com and GHES alike).
_RUN_PATH = re.compile(r"/(?P<repo>[^/]+/[^/]+)/actions/runs/(?P<id>[0-9]{1,20})/?")


NO_REPO = "no GitHub repo is set for runs: an admin sets it in Settings > Runners"


@dataclass(frozen=True, slots=True)
class GitHubTarget:
    """Where a run's workflow is dispatched: `workflow` on `ref` of `repo` (None = not set)."""

    repo: str | None
    workflow: str
    ref: str

    def problem(self) -> str | None:
        """Why this target can't be dispatched to, or None."""
        if self.repo is None:
            return NO_REPO
        if not _REPO.fullmatch(self.repo):
            return f"GitHub repo must look like owner/name, got {self.repo!r}"
        if not _WORKFLOW.fullmatch(self.workflow):
            return f"GitHub workflow must be a .yml file name, got {self.workflow!r}"
        if not self.ref.strip():
            return "GitHub workflow ref (a branch or tag) is required"
        return None


class GitHubActionsProvider:
    def __init__(
        self,
        *,
        target: Callable[[], GitHubTarget],
        token: str,
        api_url: str = DEFAULT_API_URL,
        api_version: str = DEFAULT_API_VERSION,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`target` is called at every start (the repo/workflow/ref are edited in the app, so a
        change applies to the next run without a restart)."""
        if not token.strip():
            raise ValueError("a GitHub token is required")
        if urlsplit(api_url).scheme not in ("http", "https"):
            raise ValueError(f"GitHub API URL must be http(s), got {api_url!r}")
        if not _API_VERSION.fullmatch(api_version):
            raise ValueError(f"GitHub API version must be YYYY-MM-DD, got {api_version!r}")
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version
        self._target = target
        self._token = token
        self._transport = transport

    def dispatch_url(self, target: GitHubTarget) -> str:
        return f"{self.api_url}/repos/{target.repo}/actions/workflows/{target.workflow}/dispatches"

    def runs_page_url(self, target: GitHubTarget) -> str:
        """The workflow's run list in the web UI (the ref when GitHub returns no run)."""
        return f"{web_url(self.api_url)}/{target.repo}/actions/workflows/{target.workflow}"

    def start(self, job: JobStart) -> str:
        """Dispatch the workflow for `job` to the current target. Returns the Actions run URL
        (or the workflow's runs page when GitHub doesn't return the run). Raises ProviderError if
        no repo is set or GitHub refuses."""
        inputs = {"server": job.server_url, "token": job.job_token, "run_id": str(job.run_id)}
        return self._dispatch(inputs, (self._token, job.job_token))

    def dispatch(self, inputs: dict[str, str]) -> str:
        """Dispatch the current target's workflow with `inputs` (none of them secret). Returns
        the ref like `start()` does; raises ProviderError the same way."""
        return self._dispatch(inputs, (self._token,))

    def run_id_of(self, ref: str) -> int | None:
        """The Actions run id in `ref`, or None unless its path is
        `/<owner>/<repo>/actions/runs/<id>` with owner/repo the current target repo (any case).
        Only the path is read: requests always go to the configured `api_url`, never the ref's
        host."""
        found = self._run_ref(ref)
        return None if found is None else found[1]

    def status(self, ref: str) -> JobStatus:
        """The `work` job of the Actions run `ref` names: pending until it has a machine, then
        running; ended only once every job of the run has completed (a mailbox publish job may
        still be uploading after work). Unknown when `ref` names no run (see `run_id_of`).
        Raises ProviderError if GitHub can't be asked or answers without the run's jobs."""
        url = self._run_url(ref)
        if url is None:
            return JobStatus("unknown", detail="the ref names no workflow run")
        jobs = self._get_json(f"{url}/jobs").get("jobs")
        if not isinstance(jobs, list) or not all(
            isinstance(job, dict) and isinstance(job.get("status"), str) for job in jobs
        ):
            raise ProviderError("GitHub answered 200 without the run's jobs")

        work: dict[str, Any] = next((job for job in jobs if job.get("name") == WORK_JOB), {})
        if jobs and all(job["status"] == "completed" for job in jobs):
            conclusion = work.get("conclusion")
            return JobStatus("ended", detail=conclusion if isinstance(conclusion, str) else None)
        if work.get("status", "queued") in _WAITING:
            return JobStatus("pending")
        return JobStatus("running", since=_time(work.get("started_at")))

    def cancel(self, ref: str) -> None:
        """Ask GitHub to cancel the run `ref` names (no-op if it names none). 202 and 409
        (already finished) both count as done; anything else raises ProviderError."""
        url = self._run_url(ref)
        if url is None:
            return
        resp = self._send("POST", f"{url}/cancel", None, (self._token,))
        if resp.status_code not in (202, 409):
            raise _refused(resp, (self._token,))

    def cleanup(self, ref: str) -> None:
        """Nothing to delete: Actions expires the run's artifacts itself."""

    def _run_ref(self, ref: str) -> tuple[str, int] | None:
        """(the current target repo, the run id) if `ref` names a run of that repo."""
        repo = self._target().repo
        if repo is None or not _REPO.fullmatch(repo):
            return None
        match = _RUN_PATH.fullmatch(urlsplit(ref).path)
        if match is None or match["repo"].lower() != repo.lower():
            return None
        return repo, int(match["id"])

    def _run_url(self, ref: str) -> str | None:
        found = self._run_ref(ref)
        if found is None:
            return None
        repo, run_id = found
        return f"{self.api_url}/repos/{repo}/actions/runs/{run_id}"

    def _get_json(self, url: str) -> dict[str, Any]:
        """GET `url`; a 200 that isn't a JSON object is {}. Any other answer raises a safe
        ProviderError."""
        resp = self._send("GET", url, None, (self._token,))
        if resp.status_code != 200:
            raise _refused(resp, (self._token,))
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _dispatch(self, inputs: dict[str, str], secrets: tuple[str, ...]) -> str:
        target = self._target()
        if problem := target.problem():
            raise ProviderError(problem)
        body: dict[str, Any] = {"ref": target.ref, "inputs": inputs}
        if self.api_version < _RUN_DETAILS_BUILT_IN:
            body["return_run_details"] = True
        resp = self._send("POST", self.dispatch_url(target), body, secrets)
        if resp.status_code == 204:
            return self.runs_page_url(target)
        if resp.status_code == 200:
            return _html_url(resp) or self.runs_page_url(target)
        raise _refused(resp, secrets)

    def _send(
        self, method: str, url: str, body: dict[str, Any] | None, secrets: tuple[str, ...]
    ) -> httpx.Response:
        """One request with the GitHub headers; network errors become a safe ProviderError."""
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "AgentOS",
        }
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT) as client:
                return client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            # `from None`: the chained exception carries the request, headers included.
            raise ProviderError(
                _safe(f"GitHub unreachable: {type(exc).__name__}: {exc}", secrets)
            ) from None


def web_url(api_url: str) -> str:
    """The web UI base for an API base: api.github.com -> github.com, GHES {host}/api/v3 -> host."""
    parts = urlsplit(api_url.rstrip("/"))
    if parts.hostname == "api.github.com":
        return "https://github.com"
    path = parts.path.removesuffix("/api/v3")
    return f"{parts.scheme}://{parts.netloc}{path}"


def _html_url(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    url = data.get("html_url") if isinstance(data, dict) else None
    if isinstance(url, str) and urlsplit(url).scheme in ("http", "https"):
        return url
    return None


def _time(value: object) -> datetime | None:
    """A GitHub timestamp (`2026-10-16T09:00:00Z`), or None if it isn't one."""
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _refused(resp: httpx.Response, secrets: tuple[str, ...]) -> ProviderError:
    return ProviderError(
        _safe(f"GitHub answered {resp.status_code}: {_github_message(resp)}", secrets)
    )


def _github_message(resp: httpx.Response) -> str:
    """GitHub's error `message` (plus any `errors[].message`), else the reason phrase."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    parts: list[str] = []
    if isinstance(data, dict):
        if isinstance(data.get("message"), str):
            parts.append(data["message"])
        for err in data.get("errors") or []:
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                parts.append(err["message"])
            elif isinstance(err, str):
                parts.append(err)
    return "; ".join(parts) or resp.reason_phrase or "no message"


def _safe(text: str, secrets: tuple[str, ...]) -> str:
    """Redact the tokens first (so a cut can never leave half of one), then clip to one line."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = " ".join(text.split())
    return text if len(text) <= _MAX_MESSAGE_CHARS else text[: _MAX_MESSAGE_CHARS - 1] + "…"
