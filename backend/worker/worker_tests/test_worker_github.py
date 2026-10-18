"""GitHub API publication: the pure parts as tables (mechanism choice, repo URLs, diff-tree
parsing, tree entries, classifying GitHub's answers, token scrubbing), the git side of
publishing against local repos (a rewrite or a merge is refused, the branch looks pushed
afterwards, blob reads), plus whole runs for what never reaches GitHub (the setup's env check)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentos_worker import github, publication, publishing
from agentos_worker.git import GitResult, GitRunner
from worker_tests.worker_support import (
    KEY,
    FakeServer,
    Origin,
    agent_stage,
    git,
    make_package,
    run_worker,
)

SERVER = "https://github.com"
BRANCH = f"agent/{KEY}"
A = "a" * 40
B = "b" * 40
Z = "0" * 40


@pytest.mark.parametrize(
    ("env", "api"),
    [
        ({"GITHUB_ACTIONS": "true", "GH_TOKEN": "t"}, True),
        ({"GH_TOKEN": "t"}, False),
        ({"GITHUB_ACTIONS": "true"}, False),
        ({"GITHUB_ACTIONS": "true", "GH_TOKEN": "  "}, False),
        ({"GITHUB_ACTIONS": "false", "GH_TOKEN": "t"}, False),
    ],
)
def test_api_publish_needs_actions_and_a_token(env: dict[str, str], api: bool) -> None:
    assert publication.api_publish(env) is api


@pytest.mark.parametrize(
    ("url", "server", "slug"),
    [
        ("https://github.com/acme/app", SERVER, "acme/app"),
        ("https://github.com/acme/app.git", SERVER, "acme/app"),
        ("https://github.com/acme/app/", SERVER, "acme/app"),
        ("https://github.com/acme/app.git/", SERVER, "acme/app"),
        ("https://x-access-token:secret@github.com/acme/app", SERVER, "acme/app"),
        ("https://GitHub.COM/acme/my.app", SERVER, "acme/my.app"),
        ("git@github.com:acme/app.git", SERVER, "acme/app"),
        ("github.com:acme/app", SERVER, "acme/app"),
        ("https://ghe.corp:8443/team/svc", "https://ghe.corp:8443/", "team/svc"),
    ],
)
def test_github_repo_parses_https_and_scp(url: str, server: str, slug: str) -> None:
    assert publication.github_repo(url, server) == (slug, None)


@pytest.mark.parametrize(
    ("url", "server", "word"),
    [
        ("ssh://git@github.com/acme/app", SERVER, "scp-style"),
        ("http://github.com/acme/app", SERVER, "https"),
        ("file:///srv/app.git", SERVER, "https"),
        ("/srv/app.git", SERVER, "scp-style"),
        ("https://gitlab.com/acme/app", SERVER, "gitlab.com"),
        ("https://ghe.corp/acme/app", "https://ghe.corp:8443", "host"),
        ("https://github.com/acme", SERVER, "owner/repo"),
        ("https://github.com/acme/app/tree/main", SERVER, "owner/repo"),
        ("https://github.com/acme/..", SERVER, "owner/repo"),
        ("https://github.com/acme/app?x=1", SERVER, "query"),
    ],
)
def test_github_repo_refuses_other_forms(url: str, server: str, word: str) -> None:
    slug, why = publication.github_repo(url, server)

    assert slug is None
    assert word in str(why)


def test_github_repo_errors_never_echo_userinfo() -> None:
    for url in ("https://user:hunter2@gitlab.com/a/b", "https://user:hunter2@github.com/a"):
        _, why = publication.github_repo(url, SERVER)
        assert why is not None
        assert "hunter2" not in why


def diff(old_mode: str, new_mode: str, old: str, new: str, status: str, path: str) -> bytes:
    return f":{old_mode} {new_mode} {old} {new} {status}\0{path}\0".encode()


def test_parse_diff_tree_reads_nul_separated_entries() -> None:
    out = diff("000000", "100644", Z, A, "A", "new file.txt") + diff(
        "100644", "000000", B, Z, "D", "dir/gone"
    )

    assert publication.parse_diff_tree(out) == [
        publication.DiffEntry("000000", "100644", Z, A, "A", "new file.txt"),
        publication.DiffEntry("100644", "000000", B, Z, "D", "dir/gone"),
    ]
    assert publication.parse_diff_tree(b"") == []


TEXT = b"line one\r\nline two\n"


@pytest.mark.parametrize(
    ("entry", "data", "expected", "upload"),
    [
        (
            publication.DiffEntry("000000", "100644", Z, A, "A", "a.txt"),
            TEXT,
            {"path": "a.txt", "mode": "100644", "type": "blob", "content": TEXT.decode()},
            None,
        ),
        (
            publication.DiffEntry("100644", "100755", B, A, "M", "run.sh"),
            b"#!/bin/sh\n",
            {"path": "run.sh", "mode": "100755", "type": "blob", "content": "#!/bin/sh\n"},
            None,
        ),
        (
            publication.DiffEntry("100644", "000000", B, Z, "D", "gone.txt"),
            None,
            {"path": "gone.txt", "mode": "100644", "type": "blob", "sha": None},
            None,
        ),
        (
            publication.DiffEntry("100644", "100755", A, A, "M", "tool"),
            None,
            {"path": "tool", "mode": "100755", "type": "blob", "sha": A},
            None,
        ),
        (
            publication.DiffEntry("000000", "120000", Z, A, "A", "link"),
            b"target/file",
            {"path": "link", "mode": "120000", "type": "blob", "content": "target/file"},
            None,
        ),
        (
            publication.DiffEntry("160000", "160000", B, A, "M", "vendor/lib"),
            None,
            {"path": "vendor/lib", "mode": "160000", "type": "commit", "sha": A},
            None,
        ),
        (
            publication.DiffEntry("160000", "000000", B, Z, "D", "vendor/lib"),
            None,
            {"path": "vendor/lib", "mode": "160000", "type": "commit", "sha": None},
            None,
        ),
        (
            publication.DiffEntry("000000", "100644", Z, A, "A", "logo.png"),
            b"\x89PNG\0\0",
            {"path": "logo.png", "mode": "100644", "type": "blob", "sha": A},
            b"\x89PNG\0\0",
        ),
        (
            publication.DiffEntry("000000", "100644", Z, A, "A", "latin1.txt"),
            b"caf\xe9",
            {"path": "latin1.txt", "mode": "100644", "type": "blob", "sha": A},
            b"caf\xe9",
        ),
    ],
)
def test_tree_entry(
    entry: publication.DiffEntry,
    data: bytes | None,
    expected: dict[str, object],
    upload: bytes | None,
) -> None:
    assert publication.tree_entry(entry, data) == (expected, upload)


def test_large_text_goes_as_a_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publication, "INLINE_MAX_BYTES", 4)
    entry = publication.DiffEntry("000000", "100644", Z, A, "A", "big.txt")

    assert publication.tree_entry(entry, b"12345") == (
        {"path": "big.txt", "mode": "100644", "type": "blob", "sha": A},
        b"12345",
    )


@pytest.mark.parametrize(
    ("headers", "wait"),
    [
        ({"retry-after": "7"}, 7.0),
        ({"retry-after": "0"}, publication.GITHUB_MIN_WAIT_S),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}, 30.0),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "900"}, publication.GITHUB_MIN_WAIT_S),
        ({"x-ratelimit-remaining": "12", "x-ratelimit-reset": "1030"}, None),
        ({}, None),
    ],
)
def test_retry_wait(headers: dict[str, str], wait: float | None) -> None:
    assert publication.retry_wait(headers, now=1000.0) == wait


@pytest.mark.parametrize(
    ("status", "message", "wait", "ref_write", "action"),
    [
        (422, "Update is not a fast forward", None, True, "rejected"),
        (422, "Reference already exists", None, True, "rejected"),
        (403, "API rate limit exceeded", 30.0, False, "retry"),
        (403, "secondary rate limit", 60.0, True, "retry"),
        (429, "too many requests", None, False, "retry"),
        (None, "timed out", None, False, "retry"),
        (502, "Bad Gateway", None, False, "retry"),
        (None, "timed out", None, True, "check"),
        (503, "unavailable", None, True, "check"),
        (422, "Reference update failed", None, True, "check"),
        (422, "Invalid tree info", None, False, "failed"),
        (403, "Resource not accessible by integration", None, True, "failed"),
        (404, "Not Found", None, False, "failed"),
        (401, "Bad credentials", None, False, "failed"),
    ],
)
def test_classify(
    status: int | None, message: str, wait: float | None, ref_write: bool, action: str
) -> None:
    assert publication.classify(status, message, wait, ref_write) == action


def test_scrub_redacts_before_clipping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publication, "MAX_GITHUB_MESSAGE_CHARS", 20)
    token = "ghs_" + "x" * 30

    assert publication.scrub(f"bad\n  token {token} here", (token, "")) == "bad token *** here"
    assert token[:8] not in publication.scrub("y" * 10 + token, (token,))


def test_api_mode_without_the_api_url_fails_setup(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GH_TOKEN", "ghs_secret")
    monkeypatch.setenv("GITHUB_SERVER_URL", SERVER)
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)

    assert run_worker(server, tmp_path / "ws") == 0

    done = server.complete()
    assert done is not None
    assert done["status"] == "failed"
    assert "GITHUB_API_URL" in done["error"]
    assert not origin.has_branch(BRANCH)


def test_a_rate_limited_setup_check_is_not_blamed_on_the_token(
    server: FakeServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rate_limited(self: github.GitHub, method: str, path: str, body: Any = None) -> Any:
        raise github.GitHubError(403, "API rate limit exceeded", "retry")

    monkeypatch.setattr(github.GitHub, "call", rate_limited)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GH_TOKEN", "ghs_secret")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("GITHUB_SERVER_URL", SERVER)
    server.manifest = make_package(
        [agent_stage("implement", "edit")], repo_url="https://github.com/acme/app"
    )

    assert run_worker(server, tmp_path / "ws") == 0

    done = server.complete()
    assert done is not None
    assert done["status"] == "failed"
    assert "rate limit" in done["error"]
    assert "only writes" not in done["error"]


class NoGitHub(github.GitHub):
    """A GitHub client the test expects never to be called."""

    def __init__(self) -> None:
        super().__init__("https://api.invalid", "t", "acme/app")

    def call(self, method: str, path: str, body: Any = None, *, ref_write: bool = False) -> Any:
        raise AssertionError(f"unexpected GitHub call: {method} {path}")


def runner(cwd: Path) -> GitRunner:
    def run(*args: str, stdin: bytes | None = None) -> GitResult:
        p = subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            check=False,
        )
        return GitResult(p.returncode, p.stdout, p.stderr)

    return run


def clone(origin: Origin, tmp_path: Path) -> Path:
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", origin.url, str(work))
    git(work, "switch", "-q", "-c", BRANCH)
    (work / "a.txt").write_bytes(b"one\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "one")
    return work


def test_a_rewrite_of_published_commits_is_refused_before_any_github_call(
    origin: Origin, tmp_path: Path
) -> None:
    work = clone(origin, tmp_path)
    pushed = git(work, "rev-parse", "HEAD").strip()
    git(work, "commit", "-q", "--amend", "-m", "one, amended")

    with pytest.raises(publication.PublishError, match="rejected"):
        publishing.publish_branch(runner(work), NoGitHub(), BRANCH, pushed, exists=True)


def test_a_merge_is_refused_before_any_github_call(origin: Origin, tmp_path: Path) -> None:
    work = clone(origin, tmp_path)
    pushed = git(work, "rev-parse", "HEAD").strip()
    git(work, "switch", "-q", "-c", "side", "origin/main")
    (work / "b.txt").write_bytes(b"side\n")
    git(work, "add", "b.txt")
    git(work, "commit", "-q", "-m", "side")
    git(work, "switch", "-q", BRANCH)
    git(work, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    with pytest.raises(publication.PublishError, match="merge"):
        publishing.publish_branch(runner(work), NoGitHub(), BRANCH, pushed, exists=True)


def test_a_published_head_looks_pushed(origin: Origin, tmp_path: Path) -> None:
    work = clone(origin, tmp_path)
    head = git(work, "rev-parse", "HEAD").strip()

    assert publishing.track_published(runner(work), BRANCH, head) is None

    assert git(work, "rev-parse", f"refs/remotes/origin/{BRANCH}").strip() == head
    assert git(work, "config", f"branch.{BRANCH}.remote").strip() == "origin"
    assert git(work, "config", f"branch.{BRANCH}.merge").strip() == f"refs/heads/{BRANCH}"


def test_a_different_remote_commit_is_fetched_and_becomes_head(
    origin: Origin, tmp_path: Path
) -> None:
    work = clone(origin, tmp_path)
    git(work, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    tree = git(work, "rev-parse", "HEAD^{tree}").strip()
    remote = git(origin.bare, "commit-tree", tree, "-p", "main", "-m", "as GitHub made it")
    remote = remote.strip()
    git(origin.bare, "update-ref", f"refs/heads/{BRANCH}", remote)

    assert publishing.track_published(runner(work), BRANCH, remote) is None

    assert git(work, "rev-parse", "HEAD").strip() == remote
    assert git(work, "rev-parse", f"refs/remotes/origin/{BRANCH}").strip() == remote
    assert git(work, "write-tree").strip() == tree
    assert git(work, "status", "--porcelain") == ""


def test_read_blobs_splits_cat_file_batch_by_size(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    contents = [b"\0bin\nary\n\xff\n", b"text\n"]
    shas = []
    for i, data in enumerate(contents):
        (tmp_path / f"f{i}").write_bytes(data)
        shas.append(git(tmp_path, "hash-object", "-w", f"f{i}").strip())

    assert publishing._read_blobs(runner(tmp_path), shas) == dict(zip(shas, contents, strict=True))


def test_a_token_off_actions_still_pushes_with_git(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghs_secret")
    server.manifest = make_package([agent_stage("implement", "edit")], repo_url=origin.url)

    assert run_worker(server, tmp_path / "ws") == 0

    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert origin.has_branch(BRANCH)
