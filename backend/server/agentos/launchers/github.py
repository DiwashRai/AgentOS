"""GitHubActionsLauncher (SPEC §12, M1): dispatches a workflow that runs `worker.py` on a runner.

The workflow (`backend/deploy/github/agentos-worker.yml`, installed as
`.github/workflows/agentos-worker.yml` on the target repo's default branch) masks the token,
downloads GET /api/worker/worker.py from `server_url` and runs it. GitHub queues the job, so
`max_concurrency` is None. Which repo/workflow/ref is asked of `target()` at every launch (they
are edited in Settings > Runners); the token and API URL/version are fixed (env).

REST facts (docs.github.com, checked 2026-10-11):
- `POST /repos/{repo}/actions/workflows/{workflow}/dispatches` with `{ref, inputs}`.
- API version 2022-11-28 (the default here): the optional `return_run_details: true` (added
  2026-02-19) turns the 204 No Content into a 200 with `{workflow_run_id, run_url, html_url}`.
- API version 2026-03-10 removed that parameter and always answers 200 with the run details.
- GHES may predate both; a 204 is still handled (launch_ref = the workflow's runs page).
- Fine-grained PAT: repository permission "Actions: write" on the one repo.

Neither the GitHub token nor the dispatch token ever appears in a raised message.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from agentos.models import GITHUB_REPO_PATTERN, GITHUB_WORKFLOW_PATTERN

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
# From this version on, run details are always returned and `return_run_details` is gone.
_RUN_DETAILS_BUILT_IN = "2026-03-10"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_MAX_MESSAGE_CHARS = 300

_REPO = re.compile(GITHUB_REPO_PATTERN)
_WORKFLOW = re.compile(GITHUB_WORKFLOW_PATTERN)
_API_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")


NO_REPO = "no GitHub repo is set for runs: an admin sets it in Settings > Runners"


class LaunchError(RuntimeError):
    """A dispatch GitHub didn't accept (or couldn't be sent). The message is safe to store and log
    (no tokens)."""


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


class GitHubActionsLauncher:
    name = "github"
    max_concurrency: int | None = None  # GitHub queues jobs; the runner pool is the limit

    def __init__(
        self,
        *,
        target: Callable[[], GitHubTarget],
        token: str,
        api_url: str = DEFAULT_API_URL,
        api_version: str = DEFAULT_API_VERSION,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`target` is called at every launch (the repo/workflow/ref are edited in the app, so a
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
        """The workflow's run list in the web UI (the launch_ref when GitHub returns no run)."""
        return f"{web_url(self.api_url)}/{target.repo}/actions/workflows/{target.workflow}"

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        """Dispatch the workflow for `run_id` to the current target. Returns the Actions run URL
        (or the workflow's runs page when GitHub doesn't return the run). Raises LaunchError if
        no repo is set or GitHub refuses."""
        target = self._target()
        if problem := target.problem():
            raise LaunchError(problem)
        body: dict[str, Any] = {
            "ref": target.ref,
            "inputs": {"server": server_url, "token": dispatch_token, "run_id": str(run_id)},
        }
        if self.api_version < _RUN_DETAILS_BUILT_IN:
            body["return_run_details"] = True
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "AgentOS",
        }
        secrets = (self._token, dispatch_token)
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT) as client:
                resp = client.post(self.dispatch_url(target), json=body, headers=headers)
        except httpx.HTTPError as exc:
            # `from None`: the chained exception carries the request, headers included.
            raise LaunchError(
                _safe(f"GitHub unreachable: {type(exc).__name__}: {exc}", secrets)
            ) from None

        if resp.status_code == 204:
            return self.runs_page_url(target)
        if resp.status_code == 200:
            return _html_url(resp) or self.runs_page_url(target)
        raise LaunchError(
            _safe(f"GitHub answered {resp.status_code}: {_github_message(resp)}", secrets)
        )


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
