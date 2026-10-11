"""GitHubActionsLauncher (SPEC §12, M1): dispatches a workflow that runs `worker.py` on a runner.

The workflow (`backend/deploy/github/agentos-worker.yml`, installed as
`.github/workflows/agentos-worker.yml` on the target repo's default branch) masks the token,
downloads GET /api/worker/worker.py from `server_url` and runs it. GitHub queues the job, so
`max_concurrency` is None.

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
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
# From this version on, run details are always returned and `return_run_details` is gone.
_RUN_DETAILS_BUILT_IN = "2026-03-10"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_MAX_MESSAGE_CHARS = 300

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")
_API_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LaunchError(RuntimeError):
    """A dispatch GitHub didn't accept. The message is safe to store and log (no tokens)."""


class GitHubActionsLauncher:
    name = "github"
    max_concurrency: int | None = None  # GitHub queues jobs; the runner pool is the limit

    def __init__(
        self,
        *,
        repo: str,
        workflow: str,
        ref: str,
        token: str,
        api_url: str = DEFAULT_API_URL,
        api_version: str = DEFAULT_API_VERSION,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _REPO.fullmatch(repo):
            raise ValueError(f"GitHub repo must look like owner/name, got {repo!r}")
        if not _WORKFLOW.fullmatch(workflow):
            raise ValueError(f"GitHub workflow must be a .yml file name, got {workflow!r}")
        if not ref.strip():
            raise ValueError("GitHub workflow ref (a branch or tag) is required")
        if not token.strip():
            raise ValueError("a GitHub token is required")
        if urlsplit(api_url).scheme not in ("http", "https"):
            raise ValueError(f"GitHub API URL must be http(s), got {api_url!r}")
        if not _API_VERSION.fullmatch(api_version):
            raise ValueError(f"GitHub API version must be YYYY-MM-DD, got {api_version!r}")
        self.repo = repo
        self.workflow = workflow
        self.ref = ref
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version
        self._token = token
        self._transport = transport

    @property
    def dispatch_url(self) -> str:
        return f"{self.api_url}/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches"

    @property
    def runs_page_url(self) -> str:
        """The workflow's run list in the web UI (the launch_ref when GitHub returns no run)."""
        return f"{web_url(self.api_url)}/{self.repo}/actions/workflows/{self.workflow}"

    def launch(self, run_id: int, server_url: str, dispatch_token: str) -> str:
        """Dispatch the workflow for `run_id`. Returns the Actions run URL (or the workflow's runs
        page when GitHub doesn't return the run). Raises LaunchError if GitHub refuses."""
        body: dict[str, Any] = {
            "ref": self.ref,
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
                resp = client.post(self.dispatch_url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            # `from None`: the chained exception carries the request, headers included.
            raise LaunchError(
                _safe(f"GitHub unreachable: {type(exc).__name__}: {exc}", secrets)
            ) from None

        if resp.status_code == 204:
            return self.runs_page_url
        if resp.status_code == 200:
            return _html_url(resp) or self.runs_page_url
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
