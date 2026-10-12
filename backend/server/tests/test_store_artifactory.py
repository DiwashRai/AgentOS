"""ArtifactoryStore (mailbox mode) against httpx.MockTransport (never a real Artifactory)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agentos.stores.artifactory import ArtifactoryStore
from agentos.stores.base import StoreError, StoreTooLarge

USER = "svc-agentos-user"
PASSWORD = "art-SECRET-pass-789"  # noqa: S105 - test value
BASIC = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
BASE = "https://art.corp/artifactory/generic-local/agentos/mailbox"
STORAGE = "https://art.corp/artifactory/api/storage/generic-local/agentos/mailbox"

Handler = Callable[[httpx.Request], httpx.Response]


def _store(handler: Handler, base_url: str = BASE) -> ArtifactoryStore:
    store = ArtifactoryStore(
        base_url=base_url, user=USER, password=PASSWORD, transport=httpx.MockTransport(handler)
    )
    store.retry_delays = (0.0, 0.0)
    return store


def _recording(*responses: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    """Answers with `responses` in turn (the last one repeats)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        r = responses[min(len(seen), len(responses)) - 1]
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    return handler, seen


def _assert_no_credentials(text: str) -> None:
    for secret in (USER, PASSWORD, BASIC, PASSWORD[:10]):
        assert secret not in text


# ---------------------------------------------------------------- configuration


@pytest.mark.parametrize(
    "url",
    [
        "http://art.corp/artifactory/generic-local/agentos",
        "https://art.corp/generic-local/agentos",
        "https://art.corp/artifactory/",
        "https://art.corp/artifactory/api/storage/x",
        "https://art.corp/artifactory/generic-local/../x",
        "https://art.corp/artifactory/generic-local/a?x=1",
        f"https://{USER}:{PASSWORD}@art.corp/artifactory/generic-local",
        "artifactory/generic-local",
    ],
)
def test_bad_base_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="Artifactory URL") as info:
        _store(lambda _r: httpx.Response(200), base_url=url)
    _assert_no_credentials(str(info.value))


def test_credentials_are_required() -> None:
    with pytest.raises(ValueError, match="user and password"):
        ArtifactoryStore(base_url=BASE, user="", password=PASSWORD)


@pytest.mark.parametrize(
    "path", ["", "/runs/x", "runs/../x", "..", "runs\\x", "runs//x", "./runs", "runs/a;b=c"]
)
def test_bad_paths_are_refused_without_a_request(path: str) -> None:
    handler, seen = _recording(httpx.Response(200))
    store = _store(handler)
    with pytest.raises(ValueError, match="bad store path"):
        store.put(path, b"x")
    with pytest.raises(ValueError, match="bad store path"):
        store.list_dir(path)
    assert seen == []


# ---------------------------------------------------------------- put / get / delete / list


def test_put_sends_the_bytes_with_a_checksum_and_basic_auth() -> None:
    handler, seen = _recording(httpx.Response(201))
    _store(handler).put("runs/1-0123456789abcdef/manifest.json", b'{"a": 1}')

    [req] = seen
    assert req.method == "PUT"
    assert str(req.url) == f"{BASE}/runs/1-0123456789abcdef/manifest.json"
    assert req.content == b'{"a": 1}'
    assert req.headers["X-Checksum-Sha256"] == hashlib.sha256(b'{"a": 1}').hexdigest()
    assert req.headers["Authorization"] == f"Basic {BASIC}"


def test_get_streams_to_dest(tmp_path: Path) -> None:
    handler, seen = _recording(httpx.Response(200, content=b"line1\nline2\n"))
    dest = tmp_path / "outbox.jsonl"
    assert _store(handler).get("runs/x/outbox.jsonl", dest, max_bytes=100) is True
    assert dest.read_bytes() == b"line1\nline2\n"
    assert str(seen[0].url) == f"{BASE}/runs/x/outbox.jsonl"
    assert seen[0].method == "GET"


def test_get_missing_is_false(tmp_path: Path) -> None:
    handler, _ = _recording(httpx.Response(404))
    dest = tmp_path / "outbox.jsonl"
    assert _store(handler).get("runs/x/outbox.jsonl", dest, max_bytes=100) is False
    assert not dest.exists()


def test_get_past_max_bytes_removes_dest(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A stream with no Content-Length: the cap is enforced while reading.
        return httpx.Response(200, content=iter([b"x" * 60, b"y" * 60]))

    dest = tmp_path / "outbox.jsonl"
    with pytest.raises(StoreTooLarge):
        _store(handler).get("runs/x/outbox.jsonl", dest, max_bytes=100)
    assert not dest.exists()


def test_get_refuses_a_too_large_content_length(tmp_path: Path) -> None:
    handler, seen = _recording(httpx.Response(200, content=b"z" * 101))
    dest = tmp_path / "outbox.jsonl"
    with pytest.raises(StoreTooLarge):
        _store(handler).get("runs/x/outbox.jsonl", dest, max_bytes=100)
    assert not dest.exists()
    assert len(seen) == 1  # not retried


def test_delete_is_one_request_and_missing_is_fine() -> None:
    handler, seen = _recording(httpx.Response(204), httpx.Response(404))
    store = _store(handler)
    store.delete("runs/1-0123456789abcdef")
    store.delete("runs/1-0123456789abcdef")
    assert [(r.method, str(r.url)) for r in seen] == [
        ("DELETE", f"{BASE}/runs/1-0123456789abcdef"),
    ] * 2


def test_list_dir_uses_the_storage_api() -> None:
    body = {
        "repo": "generic-local",
        "children": [
            {"uri": "/1-0123456789abcdef", "folder": True},
            {"uri": "/notes.txt", "folder": False},
            {"uri": "/../evil", "folder": True},
            {"folder": True},
        ],
    }
    handler, seen = _recording(httpx.Response(200, json=body))
    assert _store(handler).list_dir("runs") == ["1-0123456789abcdef", "notes.txt"]
    assert str(seen[0].url) == f"{STORAGE}/runs"


def test_list_dir_missing_is_empty() -> None:
    handler, _ = _recording(httpx.Response(404))
    assert _store(handler).list_dir("runs") == []


def test_list_dir_with_a_context_path_prefix() -> None:
    handler, seen = _recording(httpx.Response(200, json={"children": []}))
    store = _store(handler, base_url="https://art.corp/tools/artifactory/gen/")
    assert store.list_dir("runs") == []
    assert str(seen[0].url) == "https://art.corp/tools/artifactory/api/storage/gen/runs"


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"children": 3}'])
def test_list_dir_bad_answer_is_a_store_error(content: bytes) -> None:
    handler, _ = _recording(httpx.Response(200, content=content))
    with pytest.raises(StoreError, match="no children list"):
        _store(handler).list_dir("runs")


# ---------------------------------------------------------------- retries and errors


def test_5xx_is_retried_then_succeeds() -> None:
    handler, seen = _recording(httpx.Response(503), httpx.Response(502), httpx.Response(201))
    _store(handler).put("runs/a", b"x")
    assert len(seen) == 3


def test_5xx_three_times_is_a_store_error() -> None:
    handler, seen = _recording(httpx.Response(500, json={"errors": [{"message": "boom"}]}))
    with pytest.raises(StoreError, match="failed after 3 attempts: answered 500: boom"):
        _store(handler).put("runs/a", b"x")
    assert len(seen) == 3


def test_network_errors_are_retried(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=b"ok")

    assert _store(handler).get("runs/a", tmp_path / "a", max_bytes=10) is True
    assert len(calls) == 3


def test_4xx_is_not_retried() -> None:
    handler, seen = _recording(httpx.Response(403, json={"errors": [{"message": "Forbidden"}]}))
    with pytest.raises(StoreError, match="Artifactory put runs/a answered 403: Forbidden"):
        _store(handler).put("runs/a", b"x")
    assert len(seen) == 1


def test_errors_and_logs_never_hold_credentials(caplog: pytest.LogCaptureFixture) -> None:
    echo = f"bad login {USER}:{PASSWORD} (Basic {BASIC}) " + "x" * 400

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            raise httpx.ConnectError(f"refused {PASSWORD} {BASIC}", request=request)
        status = 503 if request.method == "DELETE" else 401
        return httpx.Response(status, json={"errors": [{"message": echo}]})

    store = _store(handler)
    messages: list[str] = []
    with caplog.at_level(logging.DEBUG):
        for call in (
            lambda: store.put("runs/a", b"x"),
            lambda: store.delete("runs/a"),
            lambda: store.list_dir("runs"),
        ):
            with pytest.raises(StoreError) as info:
                call()
            assert info.value.__cause__ is None
            assert len(str(info.value)) <= 300
            messages.append(str(info.value))
    assert any("***" in m for m in messages)
    assert caplog.records  # the retries were logged
    _assert_no_credentials("\n".join(messages + [r.getMessage() for r in caplog.records]))
    _assert_no_credentials(repr(store) + json.dumps(store.base_url))
