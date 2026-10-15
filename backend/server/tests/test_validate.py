"""Pure validators (SPEC §6, §14 "Pure units")."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from agentos.errors import ApiError
from agentos.models import ValidationIssue, matching
from agentos.validate import (
    MAX_BUNDLE_BYTES,
    MAX_FILE_BYTES,
    base_branch_problem,
    block_markers,
    check_bundle_size,
    check_file_size,
    check_unique_paths,
    parsed,
    repo_url_problem,
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


# ------------------------------------------------------------------ pydantic errors as UI text


class _Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=5)
    timeout: int = Field(ge=60, le=100)
    tags: list[str] = Field(max_length=2)
    prefix: Annotated[str, matching(r"^[A-Z]+$", "Capital letters only.")]


_VALID: dict[str, Any] = {"name": "n", "timeout": 60, "tags": [], "prefix": "AOS"}


@pytest.mark.parametrize(
    ("change", "field", "message"),
    [
        ({"name": ""}, "name", "Required."),
        ({"name": "abcdef"}, "name", "At most 5 characters."),
        ({"name": None}, "name", "Required."),  # missing (dropped below)
        ({"timeout": 59}, "timeout", "At least 60."),
        ({"timeout": 101}, "timeout", "At most 100."),
        ({"timeout": "soon"}, "timeout", "Must be a whole number."),
        ({"tags": ["a", "b", "c"]}, "tags", "At most 2 items."),
        ({"prefix": "aos"}, "prefix", "Capital letters only."),
        ({"prefix": "AOS\n"}, "prefix", "Capital letters only."),  # `$` doesn't skip a newline
        ({"prefix": ""}, "prefix", "Required."),
        ({"owner": "me"}, "owner", "Unknown field."),
    ],
)
def test_pydantic_errors_read_as_ui_text(change: dict[str, Any], field: str, message: str) -> None:
    data = {k: v for k, v in {**_VALID, **change}.items() if v is not None}
    model, issues = parsed(_Rules, data, ("value",))
    assert model is None
    assert issues == [ValidationIssue(loc=["value", field], message=message)]


def test_matching_keeps_what_the_pattern_accepts() -> None:
    model, issues = parsed(_Rules, _VALID)
    assert (model, issues) == (_Rules(**_VALID), [])


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
    assert repo_url_problem(url) is None
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
    assert repo_url_problem(url) is not None


@pytest.mark.parametrize(
    ("check", "value", "field"),
    [
        (validate_repo_url, "file:///x", "repo_url"),
        (validate_base_branch, "a..b", "base_branch"),
    ],
)
def test_a_wrapper_rejects_at_its_field(
    check: Callable[[str], str], value: str, field: str
) -> None:
    with pytest.raises(ApiError) as e:
        check(value)
    assert (e.value.status, e.value.code) == (422, "validation_error")
    assert [issue.loc for issue in e.value.issues] == [[field]]
    assert e.value.message.startswith(f"{field}: ")


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
    assert base_branch_problem(branch) is None
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
    assert base_branch_problem(branch) is not None


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
