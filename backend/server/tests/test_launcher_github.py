"""GitHubActionsLauncher (SPEC §12) against httpx.MockTransport (never the real GitHub), plus the
worker workflow template's hygiene and its installed copy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentos.launchers.github import GitHubActionsLauncher, LaunchError, web_url

GH_TOKEN = "github_pat_SECRET123"  # noqa: S105 - test value
DISPATCH_TOKEN = "dispatch-SECRET-456"  # noqa: S105 - test value
SERVER = "http://172.20.0.1:8080"
RUN_HTML = "https://github.com/DiwashRai/AgentOS/actions/runs/987654321"
RUNS_PAGE = "https://github.com/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml"

BACKEND = Path(__file__).resolve().parents[2]
TEMPLATE = BACKEND / "deploy" / "github" / "agentos-worker.yml"
INSTALLED = BACKEND.parent / ".github" / "workflows" / "agentos-worker.yml"

Handler = Callable[[httpx.Request], httpx.Response]


def _launcher(handler: Handler, **kw: Any) -> GitHubActionsLauncher:
    args: dict[str, Any] = {
        "repo": "DiwashRai/AgentOS",
        "workflow": "agentos-worker.yml",
        "ref": "main",
        "token": GH_TOKEN,
    }
    args.update(kw)
    return GitHubActionsLauncher(**args, transport=httpx.MockTransport(handler))


def _recording(response: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return handler, seen


def _run_details() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "workflow_run_id": 987654321,
            "run_url": "https://api.github.com/repos/DiwashRai/AgentOS/actions/runs/987654321",
            "html_url": RUN_HTML,
        },
    )


# ---------------------------------------------------------------- the dispatch request


def test_request_shape() -> None:
    handler, seen = _recording(_run_details())
    launcher = _launcher(handler)

    assert launcher.name == "github"
    assert launcher.max_concurrency is None
    assert launcher.launch(42, SERVER, DISPATCH_TOKEN) == RUN_HTML

    [req] = seen
    assert req.method == "POST"
    assert str(req.url) == (
        "https://api.github.com/repos/DiwashRai/AgentOS/actions/workflows/"
        "agentos-worker.yml/dispatches"
    )
    assert req.headers["Authorization"] == f"Bearer {GH_TOKEN}"
    assert req.headers["Accept"] == "application/vnd.github+json"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(req.content) == {
        "ref": "main",
        "inputs": {"server": SERVER, "token": DISPATCH_TOKEN, "run_id": "42"},
        "return_run_details": True,
    }


def test_api_version_2026_drops_return_run_details() -> None:
    # 2026-03-10 removed the parameter: run details are always returned.
    handler, seen = _recording(_run_details())
    launcher = _launcher(handler, api_version="2026-03-10")

    assert launcher.launch(7, SERVER, DISPATCH_TOKEN) == RUN_HTML
    [req] = seen
    assert req.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert "return_run_details" not in json.loads(req.content)


def test_ghes_api_url() -> None:
    handler, seen = _recording(httpx.Response(204))
    launcher = _launcher(handler, api_url="https://ghe.example.com/api/v3/")

    ref = launcher.launch(3, SERVER, DISPATCH_TOKEN)

    assert str(seen[0].url).startswith("https://ghe.example.com/api/v3/repos/DiwashRai/AgentOS/")
    assert ref == "https://ghe.example.com/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml"


# ---------------------------------------------------------------- launch_ref


def test_204_falls_back_to_the_workflow_runs_page() -> None:
    handler, _ = _recording(httpx.Response(204))
    assert _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN) == RUNS_PAGE


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"workflow_run_id": 1}),
        httpx.Response(200, json={"html_url": "javascript:alert(1)"}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, content=b"not json"),
    ],
)
def test_200_without_a_usable_html_url_falls_back(response: httpx.Response) -> None:
    handler, _ = _recording(response)
    assert _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN) == RUNS_PAGE


@pytest.mark.parametrize(
    ("api_url", "web"),
    [
        ("https://api.github.com", "https://github.com"),
        ("https://api.github.com/", "https://github.com"),
        ("https://ghe.example.com/api/v3", "https://ghe.example.com"),
        ("http://10.0.0.5:8443/api/v3/", "http://10.0.0.5:8443"),
    ],
)
def test_web_url(api_url: str, web: str) -> None:
    assert web_url(api_url) == web


# ---------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(404, json={"message": "Not Found", "documentation_url": "x"}),
            "GitHub answered 404: Not Found",
        ),
        (
            httpx.Response(
                422,
                json={
                    "message": "Validation Failed",
                    "errors": [{"message": 'Unexpected inputs provided: ["x"]'}, "plain"],
                },
            ),
            'GitHub answered 422: Validation Failed; Unexpected inputs provided: ["x"]; plain',
        ),
        (httpx.Response(401, json={"message": "Bad credentials"}), "GitHub answered 401"),
        (httpx.Response(502, content=b"<html>bad gateway</html>"), "GitHub answered 502: Bad"),
        (httpx.Response(301, headers={"Location": "https://x"}), "GitHub answered 301"),
    ],
)
def test_non_2xx_raises(response: httpx.Response, expected: str) -> None:
    handler, _ = _recording(response)
    with pytest.raises(LaunchError) as info:
        _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN)
    assert str(info.value).startswith(expected)


def test_error_messages_never_contain_a_token() -> None:
    # Whatever GitHub echoes back (even both tokens, long enough to be clipped), the message
    # names the status and GitHub's message with the tokens redacted.
    echo = f"bad token {GH_TOKEN} for input {DISPATCH_TOKEN} " + "x" * 400 + GH_TOKEN
    handler, _ = _recording(httpx.Response(403, json={"message": echo}))
    with pytest.raises(LaunchError) as info:
        _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN)
    message = str(info.value)
    assert message.startswith("GitHub answered 403: bad token *** for input *** xxx")
    assert len(message) <= 300
    for secret in (GH_TOKEN, DISPATCH_TOKEN, GH_TOKEN[:12], DISPATCH_TOKEN[:12]):
        assert secret not in message


def test_network_error_is_a_launch_error_without_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed ({DISPATCH_TOKEN}, {GH_TOKEN})", request=request)

    with pytest.raises(LaunchError) as info:
        _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN)
    message = str(info.value)
    assert message.startswith("GitHub unreachable: ConnectError: connect failed")
    assert GH_TOKEN not in message
    assert DISPATCH_TOKEN not in message
    # No chained exception: the original holds the request, Authorization header included.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_timeout_is_a_launch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LaunchError, match="GitHub unreachable: ReadTimeout"):
        _launcher(handler).launch(1, SERVER, DISPATCH_TOKEN)


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"repo": "AgentOS"}, "owner/name"),
        ({"repo": "a/b/c"}, "owner/name"),
        ({"workflow": "../x.yml"}, ".yml file name"),
        ({"workflow": "agentos-worker"}, ".yml file name"),
        ({"ref": " "}, "ref"),
        ({"token": ""}, "token is required"),
        ({"api_url": "api.github.com"}, "http"),
        ({"api_version": "latest"}, "YYYY-MM-DD"),
    ],
)
def test_constructor_rejects_bad_config(kw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _launcher(lambda _req: httpx.Response(204), **kw)


def test_token_is_not_in_the_repr_or_attributes() -> None:
    launcher = _launcher(lambda _req: httpx.Response(204))
    assert GH_TOKEN not in repr(launcher)
    public = {k: v for k, v in vars(launcher).items() if not k.startswith("_")}
    assert GH_TOKEN not in repr(public)


# ---------------------------------------------------------------- the workflow template


def test_installed_workflow_matches_the_template() -> None:
    if not INSTALLED.is_file():
        pytest.skip(f"{INSTALLED} is absent (e.g. a container build)")
    assert INSTALLED.read_bytes() == TEMPLATE.read_bytes(), (
        "copy backend/deploy/github/agentos-worker.yml to .github/workflows/ (byte-identical)"
    )


def test_workflow_declares_the_launcher_inputs() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    block = text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
    declared = re.findall(r"^      ([a-z_]+):$", block, flags=re.MULTILINE)
    # The launcher sends exactly these (see test_request_shape).
    assert declared == ["server", "token", "run_id"]
    assert "runs-on: [self-hosted, agentos]" in text
    # Repo access is only this job's token, limited to pushing the branch and its PR.
    assert "permissions:\n  contents: write\n  pull-requests: write\n" in text
    assert "GH_TOKEN: ${{ github.token }}" in text


def test_workflow_never_interpolates_inputs_into_scripts() -> None:
    # `${{ }}` in a `run:` block is a script-injection hole, and the token must never be
    # expanded at all: a step's env is printed before the mask exists.
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "inputs.token" not in text
    expression_lines = [line.strip() for line in text.splitlines() if "${{" in line]
    for line in expression_lines:
        if line.startswith("#"):
            continue
        assert re.fullmatch(
            r'run-name: "AgentOS run #\$\{\{ inputs\.run_id \}\}"'
            r"|AOS_(SERVER|RUN_ID): \$\{\{ inputs\.(server|run_id) \}\}"
            # GitHub masks the job token itself (a secret), so it may sit in a step's env.
            r"|GH_TOKEN: \$\{\{ github\.token \}\}",
            line,
        ), line
    mask = text.index("::add-mask::")
    assert mask < text.index("curl ") < text.index("AGENTOS_TOKEN=")
