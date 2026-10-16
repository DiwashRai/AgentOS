"""GitHubActionsProvider against httpx.MockTransport (never the real GitHub): the dispatch, the
job-level status over recorded answers (`fixtures/github/`), cancel, plus the worker workflow
template's hygiene and its installed copy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentos.launchers.base import JobStart, JobStatus, Provider, ProviderError
from agentos.launchers.github import NO_REPO, GitHubActionsProvider, GitHubTarget, web_url

GH_TOKEN = "github_pat_SECRET123"  # noqa: S105 - test value
DISPATCH_TOKEN = "dispatch-SECRET-456"  # noqa: S105 - test value
SERVER = "http://172.20.0.1:8080"
RUN_HTML = "https://github.com/DiwashRai/AgentOS/actions/runs/987654321"
RUNS_PAGE = "https://github.com/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml"

BACKEND = Path(__file__).resolve().parents[2]
TEMPLATE = BACKEND / "deploy" / "github" / "agentos-worker.yml"
INSTALLED = BACKEND.parent / ".github" / "workflows" / "agentos-worker.yml"
RECORDED = Path(__file__).parent / "fixtures" / "github"

Handler = Callable[[httpx.Request], httpx.Response]


TARGET = GitHubTarget(repo="DiwashRai/AgentOS", workflow="agentos-worker.yml", ref="main")


_: Provider = GitHubActionsProvider(target=lambda: TARGET, token=GH_TOKEN)


def _provider(handler: Handler, **kw: Any) -> GitHubActionsProvider:
    """`repo`/`workflow`/`ref` in `kw` change the target; the rest are constructor arguments."""
    fields = {k: kw.pop(k) for k in ("repo", "workflow", "ref") if k in kw}
    target = replace(TARGET, **fields)
    args: dict[str, Any] = {"target": lambda: target, "token": GH_TOKEN}
    args.update(kw)
    return GitHubActionsProvider(**args, transport=httpx.MockTransport(handler))


def job(run_id: int) -> JobStart:
    return JobStart(f"{run_id}-1-0123456789abcdef", run_id, SERVER, DISPATCH_TOKEN)


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
    provider = _provider(handler)

    assert provider.start(job(42)) == RUN_HTML

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
    provider = _provider(handler, api_version="2026-03-10")

    assert provider.start(job(7)) == RUN_HTML
    [req] = seen
    assert req.headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert "return_run_details" not in json.loads(req.content)


def test_ghes_api_url() -> None:
    handler, seen = _recording(httpx.Response(204))
    provider = _provider(handler, api_url="https://ghe.example.com/api/v3/")

    ref = provider.start(job(3))

    assert str(seen[0].url).startswith("https://ghe.example.com/api/v3/repos/DiwashRai/AgentOS/")
    assert ref == "https://ghe.example.com/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml"


# ---------------------------------------------------------------- the ref


def test_204_falls_back_to_the_workflow_runs_page() -> None:
    handler, _ = _recording(httpx.Response(204))
    assert _provider(handler).start(job(1)) == RUNS_PAGE


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
    assert _provider(handler).start(job(1)) == RUNS_PAGE


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
    with pytest.raises(ProviderError) as info:
        _provider(handler).start(job(1))
    assert str(info.value).startswith(expected)


def test_error_messages_never_contain_a_token() -> None:
    # Whatever GitHub echoes back (even both tokens, long enough to be clipped), the message
    # names the status and GitHub's message with the tokens redacted.
    echo = f"bad token {GH_TOKEN} for input {DISPATCH_TOKEN} " + "x" * 400 + GH_TOKEN
    handler, _ = _recording(httpx.Response(403, json={"message": echo}))
    with pytest.raises(ProviderError) as info:
        _provider(handler).start(job(1))
    message = str(info.value)
    assert message.startswith("GitHub answered 403: bad token *** for input *** xxx")
    assert len(message) <= 300
    for secret in (GH_TOKEN, DISPATCH_TOKEN, GH_TOKEN[:12], DISPATCH_TOKEN[:12]):
        assert secret not in message


def test_network_error_is_a_provider_error_without_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed ({DISPATCH_TOKEN}, {GH_TOKEN})", request=request)

    with pytest.raises(ProviderError) as info:
        _provider(handler).start(job(1))
    message = str(info.value)
    assert message.startswith("GitHub unreachable: ConnectError: connect failed")
    assert GH_TOKEN not in message
    assert DISPATCH_TOKEN not in message
    # No chained exception: the original holds the request, Authorization header included.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_timeout_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderError, match="GitHub unreachable: ReadTimeout"):
        _provider(handler).start(job(1))


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"token": ""}, "token is required"),
        ({"api_url": "api.github.com"}, "http"),
        ({"api_version": "latest"}, "YYYY-MM-DD"),
    ],
)
def test_constructor_rejects_bad_config(kw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _provider(lambda _req: httpx.Response(204), **kw)


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"repo": None}, NO_REPO),
        ({"repo": "AgentOS"}, "owner/name"),
        ({"repo": "a/b/c"}, "owner/name"),
        ({"workflow": "../x.yml"}, ".yml file name"),
        ({"workflow": "agentos-worker"}, ".yml file name"),
        ({"ref": " "}, "ref"),
    ],
)
def test_a_bad_target_fails_the_start_without_calling_github(
    kw: dict[str, Any], match: str
) -> None:
    handler, seen = _recording(httpx.Response(204))
    with pytest.raises(ProviderError, match=re.escape(match)):
        _provider(handler, **kw).start(job(1))
    assert seen == []


def test_the_target_is_read_at_every_start() -> None:
    """Settings > Runners is edited in the app: the next start uses the new repo/workflow/ref."""
    handler, seen = _recording(httpx.Response(204))
    current = [TARGET]
    provider = GitHubActionsProvider(
        target=lambda: current[0], token=GH_TOKEN, transport=httpx.MockTransport(handler)
    )
    assert provider.start(job(1)) == RUNS_PAGE
    current[0] = GitHubTarget(repo="o/other", workflow="w.yaml", ref="release")
    assert provider.start(job(2)) == ("https://github.com/o/other/actions/workflows/w.yaml")
    assert [str(r.url) for r in seen] == [
        "https://api.github.com/repos/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml/"
        "dispatches",
        "https://api.github.com/repos/o/other/actions/workflows/w.yaml/dispatches",
    ]
    assert json.loads(seen[1].content)["ref"] == "release"


def test_token_is_not_in_the_repr_or_attributes() -> None:
    provider = _provider(lambda _req: httpx.Response(204))
    assert GH_TOKEN not in repr(provider)
    public = {k: v for k, v in vars(provider).items() if not k.startswith("_")}
    assert GH_TOKEN not in repr(public)


# ---------------------------------------------------------------- mailbox mode


def test_dispatch_sends_the_given_inputs() -> None:
    handler, seen = _recording(_run_details())
    inputs = {"run": "42-0123456789abcdef", "worker_sha256": "a" * 64, "manifest_sha256": "b" * 64}

    assert _provider(handler).dispatch(inputs) == RUN_HTML

    [req] = seen
    assert req.method == "POST"
    assert str(req.url).endswith(
        "/repos/DiwashRai/AgentOS/actions/workflows/agentos-worker.yml/dispatches"
    )
    assert req.headers["Authorization"] == f"Bearer {GH_TOKEN}"
    assert json.loads(req.content) == {"ref": "main", "inputs": inputs, "return_run_details": True}


def test_dispatch_204_and_errors_match_start() -> None:
    handler, _ = _recording(httpx.Response(204))
    assert _provider(handler).dispatch({"run": "1-0123456789abcdef"}) == RUNS_PAGE

    handler, _ = _recording(httpx.Response(403, json={"message": f"nope {GH_TOKEN}"}))
    with pytest.raises(ProviderError, match=r"^GitHub answered 403: nope \*\*\*$"):
        _provider(handler).dispatch({"run": "1-0123456789abcdef"})

    handler, seen = _recording(httpx.Response(204))
    with pytest.raises(ProviderError, match=re.escape(NO_REPO)):
        _provider(handler, repo=None).dispatch({})
    assert seen == []


GHES_API = "https://ghe.corp/api/v3"
GHES_RUN = "https://ghe.corp/DiwashRai/AgentOS/actions/runs/555"


# ---------------------------------------------------------------- status and cancel


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        ("worker_no_jobs_yet", JobStatus("pending")),
        ("worker_queued", JobStatus("pending")),  # a queued job has a started_at too
        ("worker_in_progress", JobStatus("running")),
        ("worker_completed", JobStatus("ended", detail="success")),
        ("mailbox_fetch_running", JobStatus("pending")),  # the work job, not the run
        # work completed while publish waits (e.g. on its environment's approval): not ended
        ("mailbox_publish_waiting", JobStatus("running")),
        ("mailbox_completed", JobStatus("ended", detail="failure")),
    ],
)
def test_status_is_the_work_jobs(recorded: str, expected: JobStatus) -> None:
    answer = json.loads((RECORDED / f"{recorded}.json").read_text(encoding="utf-8"))
    handler, seen = _recording(httpx.Response(200, json=answer))

    assert _provider(handler).status(RUN_HTML) == expected
    [req] = seen
    assert (req.method, req.url.path) == (
        "GET",
        "/repos/DiwashRai/AgentOS/actions/runs/987654321/jobs",
    )
    assert req.headers["Authorization"] == f"Bearer {GH_TOKEN}"


@pytest.mark.parametrize(
    ("api_url", "ref", "expected_url"),
    [
        (GHES_API, GHES_RUN, "https://ghe.corp/api/v3/repos/DiwashRai/AgentOS/actions/runs/555"),
        # Owner/repo compare case-insensitively; the request uses the configured repo's spelling.
        (
            "https://api.github.com",
            "https://github.com/diwashrai/agentos/actions/runs/7/",
            "https://api.github.com/repos/DiwashRai/AgentOS/actions/runs/7",
        ),
        # Only the ref's path is read, never its host.
        (
            GHES_API,
            "https://evil.example/DiwashRai/AgentOS/actions/runs/9",
            f"{GHES_API}/repos/DiwashRai/AgentOS/actions/runs/9",
        ),
    ],
)
def test_requests_go_to_the_configured_api(api_url: str, ref: str, expected_url: str) -> None:
    handler, seen = _recording(httpx.Response(202, json={"jobs": []}))
    provider = _provider(handler, api_url=api_url)
    provider.cancel(ref)
    [req] = seen
    assert (req.method, str(req.url)) == ("POST", f"{expected_url}/cancel")


@pytest.mark.parametrize(
    "ref",
    [
        RUNS_PAGE,  # the 204 fallback names no run
        "https://github.com/someone/else/actions/runs/1",
        "https://evil.example/attacker/repo/actions/runs/1",  # a crafted ref on another host
        "https://github.com/DiwashRai/AgentOS/actions/runs/1/../../../../x",
        "https://github.com/DiwashRai/AgentOS/actions/runs/abc",
        "https://github.com/x/DiwashRai/AgentOS/actions/runs/1",
        "",
        "not a url",
    ],
)
def test_a_ref_that_names_no_run_is_unknown_and_never_asked(ref: str) -> None:
    handler, seen = _recording(httpx.Response(200, json={"jobs": []}))
    provider = _provider(handler)

    assert provider.run_id_of(ref) is None
    assert provider.status(ref).state == "unknown"
    provider.cancel(ref)
    provider.cleanup(ref)
    assert seen == []


def test_no_repo_set_means_no_run() -> None:
    handler, seen = _recording(httpx.Response(200, json={"jobs": []}))
    assert _provider(handler, repo=None).status(RUN_HTML).state == "unknown"
    assert seen == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"status": "completed"}),  # the run, not its jobs
        httpx.Response(200, json={"jobs": [{"name": "work"}]}),
    ],
)
def test_status_bad_answer_is_a_provider_error(response: httpx.Response) -> None:
    handler, _ = _recording(response)
    with pytest.raises(ProviderError, match="jobs"):
        _provider(handler).status(RUN_HTML)


def test_status_errors_are_safe() -> None:
    handler, _ = _recording(httpx.Response(404, json={"message": f"Not Found {GH_TOKEN}"}))
    with pytest.raises(ProviderError, match=r"^GitHub answered 404: Not Found \*\*\*$"):
        _provider(handler).status(RUN_HTML)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {GH_TOKEN}", request=request)

    with pytest.raises(ProviderError) as info:
        _provider(unreachable).status(RUN_HTML)
    assert str(info.value).startswith("GitHub unreachable: ConnectError")
    assert GH_TOKEN not in str(info.value)
    assert info.value.__cause__ is None


@pytest.mark.parametrize("status", [202, 409])
def test_cancel_is_done_on_202_or_409(status: int) -> None:
    handler, seen = _recording(httpx.Response(status, json={}))
    _provider(handler).cancel(RUN_HTML)
    assert [r.method for r in seen] == ["POST"]


def test_cancel_other_answers_raise() -> None:
    handler, _ = _recording(httpx.Response(403, json={"message": "Resource not accessible"}))
    with pytest.raises(ProviderError, match="GitHub answered 403: Resource not accessible"):
        _provider(handler).cancel(RUN_HTML)


# ---------------------------------------------------------------- the workflow template


def test_installed_workflow_matches_the_template() -> None:
    if not INSTALLED.is_file():
        pytest.skip(f"{INSTALLED} is absent (e.g. a container build)")
    assert INSTALLED.read_bytes() == TEMPLATE.read_bytes(), (
        "copy backend/deploy/github/agentos-worker.yml to .github/workflows/ (byte-identical)"
    )


def test_workflow_declares_the_start_inputs() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    block = text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
    declared = re.findall(r"^      ([a-z_]+):$", block, flags=re.MULTILINE)
    # `start` sends exactly these (see test_request_shape).
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
