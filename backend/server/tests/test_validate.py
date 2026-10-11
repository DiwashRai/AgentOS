"""Pure validators (SPEC §6, §14 "Pure units")."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentos.errors import ApiError
from agentos.validate import (
    MAX_BUNDLE_BYTES,
    MAX_FILE_BYTES,
    block_markers,
    check_bundle_size,
    check_file_size,
    check_unique_paths,
    validate_base_branch,
    validate_block_content,
    validate_key,
    validate_path,
    validate_pr_url,
    validate_repo_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _rejects(fn: object, value: str, code: str = "validation_error", status: int = 422) -> str:
    assert callable(fn)
    with pytest.raises(ApiError) as e:
        fn(value)
    assert (e.value.status, e.value.code) == (status, code)
    return e.value.message


# ------------------------------------------------------------------ paths


def _path_cases() -> list[Any]:
    """fixtures/path_cases.json, shared with the worker suite (worker_tests/test_worker_paths.py),
    so the two validators can't drift apart."""
    cases = json.loads((FIXTURES / "path_cases.json").read_text(encoding="utf-8"))
    return [pytest.param(c["path"], c["ok"], id=repr(c["path"])[:40]) for c in cases]


@pytest.mark.parametrize(("path", "ok"), _path_cases())
def test_path_rules(path: str, ok: bool) -> None:
    if ok:
        assert validate_path(path) == path
    else:
        _rejects(validate_path, path)


def test_unique_paths_case_insensitive() -> None:
    check_unique_paths(["a.md", "b.md", "A/b.md"])
    with pytest.raises(ApiError) as e:
        check_unique_paths(["Agents.md", "x", "AGENTS.md"])
    assert e.value.code == "validation_error"
    assert "AGENTS.md" in e.value.message


# ------------------------------------------------------------------ keys


@pytest.mark.parametrize("key", ["AOS-1", "AOS-42", "A1-0", "PROJ2-12345"])
def test_valid_keys(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "aos-1",
        "AOS",
        "AOS-",
        "1AOS-1",
        "AOS-1a",
        "AOS_1",
        "AOS-1\n",
        "AOS-" + chr(0x0661),  # ARABIC-INDIC DIGIT ONE: \d would accept it
        "A" * 40 + "-1",
    ],
)
def test_invalid_keys(key: str) -> None:
    _rejects(validate_key, key)


# ------------------------------------------------------------------ repo URLs


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "https://github.com/org/repo",
        "https://git.example.com:8443/team/sub/repo.git",
        "ssh://git@github.com/org/repo.git",
        "ssh://github.com:22/org/repo.git",
        "git@github.com:org/repo.git",
        "git@github.com:repo.git",
    ],
)
def test_valid_repo_urls(url: str) -> None:
    assert validate_repo_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "-uhttps://github.com/org/repo",
        "--upload-pack=x",
        "https://user:pw@github.com/org/repo.git",
        "https://token@github.com/org/repo.git",
        "https://git@github.com/org/repo.git",
        "ssh://bob@host/org/repo.git",
        "ssh://git:pw@host/org/repo.git",
        "http://github.com/org/repo.git",
        "file:///tmp/repo",
        "ext::sh -c touch% /tmp/pwned",
        "ext::ssh",
        "git://github.com/org/repo.git",
        "https://github.com",
        "https://github.com/",
        "https:///org/repo",
        "https://-host/org/repo",
        "https://github.com/org/repo.git?x=1",
        "https://github.com/org/repo.git#frag",
        "https://github.com/org/my repo.git",
        "https://github.com:abc/org/repo",
        "git@github.com:-oProxyCommand=x",
        "git@-host:org/repo",
        "bob@github.com:org/repo.git",
        "/local/path/repo",
        "../repo",
    ],
)
def test_invalid_repo_urls(url: str) -> None:
    _rejects(validate_repo_url, url)


def test_repo_url_message_names_field() -> None:
    assert _rejects(validate_repo_url, "file:///x").startswith("repo_url: ")


# ------------------------------------------------------------------ PR URLs


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/pull/7",
        "https://ghe.example.com:8443/o/r/pull/12",
        "https://dev.azure.com/org/p/_git/r/pullrequest/3?x=1",
    ],
)
def test_valid_pr_urls(url: str) -> None:
    assert validate_pr_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://github.com/o/r/pull/7",
        "javascript:alert(1)",
        "HTTPS://github.com/o/r/pull/7",
        "https://user:pw@github.com/o/r/pull/7",
        "https://token@github.com/o/r/pull/7",
        "https:///o/r/pull/7",
        "https://github.com/o/r/pull/7 x",
        "https://github.com/o/r/pull/7\n",
        "https://github.com/o/r/pull/\x007",
        "https://github.com:x/o/r",
        "https://[::1/o",
        "https://github.com/" + "a" * 500,
    ],
)
def test_invalid_pr_urls(url: str) -> None:
    assert _rejects(validate_pr_url, url).startswith("pr_url: ")


# ------------------------------------------------------------------ branches


@pytest.mark.parametrize(
    "branch", ["main", "develop", "release/1.2", "feature/a-b_c.d", "v2", "A" * 200]
)
def test_valid_branches(branch: str) -> None:
    assert validate_base_branch(branch) == branch


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "main\n",
        "-main",
        ".hidden",
        "/main",
        "main/",
        "main.",
        "main.lock",
        "a..b",
        "a//b",
        "a/.b",
        "a.lock/b",
        "has space",
        "a~1",
        "a^",
        "a:b",
        "a@{1}",
        "a*",
        "A" * 201,
    ],
)
def test_invalid_branches(branch: str) -> None:
    _rejects(validate_base_branch, branch)


# ------------------------------------------------------------------ block content & sizes


def test_block_markers_format() -> None:
    assert block_markers("AOS-1") == ("<!-- agentos:begin AOS-1 -->", "<!-- agentos:end AOS-1 -->")


@pytest.mark.parametrize(
    "content",
    ["", "@AGENTS.md", "plain\ntext\r\nwith <!-- comments -->", "agentos:begin without comment"],
)
def test_valid_block_content(content: str) -> None:
    validate_block_content(content)


@pytest.mark.parametrize(
    "content",
    [
        "<!-- agentos:begin AOS-1 -->",
        "x\n<!-- agentos:end AOS-1 -->\ny",
        "  <!--agentos:begin OTHER-2 -->  ",
        "a\r\n<!-- AGENTOS:END X-1 -->",
    ],
)
def test_block_content_rejects_markers(content: str) -> None:
    _rejects(validate_block_content, content)


def test_file_size_limit() -> None:
    assert check_file_size("x" * MAX_FILE_BYTES) == MAX_FILE_BYTES
    _rejects(check_file_size, "x" * (MAX_FILE_BYTES + 1), code="too_large", status=413)
    # UTF-8 bytes, not characters
    _rejects(check_file_size, "\u00e9" * (MAX_FILE_BYTES // 2 + 1), code="too_large", status=413)


def test_lone_surrogate_is_not_utf8() -> None:
    _rejects(check_file_size, "a\ud800b")


def test_bundle_size_limit() -> None:
    chunk = "x" * MAX_FILE_BYTES
    assert check_bundle_size([chunk] * 20) == MAX_BUNDLE_BYTES
    with pytest.raises(ApiError) as e:
        check_bundle_size([chunk] * 20 + ["x"])
    assert (e.value.status, e.value.code) == (413, "too_large")
