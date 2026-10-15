"""GitHub API publication: the pure parts as tables (mechanism choice, repo URLs, commit and
diff-tree parsing, tree entries, classifying GitHub's answers, token scrubbing), plus whole runs
for what never reaches GitHub (the setup's env check)."""

from __future__ import annotations

from pathlib import Path

import pytest

import worker
from worker_tests.worker_support import (
    KEY,
    FakeServer,
    Origin,
    agent_stage,
    make_manifest,
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
    assert worker.api_publish(env) is api


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
    assert worker.github_repo(url, server) == (slug, None)


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
    slug, why = worker.github_repo(url, server)

    assert slug is None
    assert word in str(why)


def test_github_repo_errors_never_echo_userinfo() -> None:
    for url in ("https://user:hunter2@gitlab.com/a/b", "https://user:hunter2@github.com/a"):
        _, why = worker.github_repo(url, SERVER)
        assert why is not None
        assert "hunter2" not in why


def commit_raw(*headers: bytes, message: bytes = b"subject\n\nbody\n") -> bytes:
    return b"\n".join(headers) + b"\n\n" + message


TREE = b"tree " + B.encode()
AUTHOR = b"author Ann <ann@x.org> 1700000000 +0130"
COMMITTER = b"committer Bob <bob@x.org> 1700000100 -0700"


def test_parse_commit_keeps_message_people_and_offsets() -> None:
    raw = commit_raw(
        TREE,
        b"parent " + A.encode(),
        b"parent " + Z.encode(),
        AUTHOR,
        COMMITTER,
        b"gpgsig -----BEGIN PGP SIGNATURE-----",
        b" abc",
        b" -----END PGP SIGNATURE-----",
        message=b"  wip\r\n\n\ttrailing  \n\n",
    )

    commit = worker.parse_commit(A, raw)

    assert commit == worker.GitCommit(
        tree=B,
        parents=(A, Z),
        author=worker.Person("Ann", "ann@x.org", "2023-11-14T23:43:20+01:30"),
        committer=worker.Person("Bob", "bob@x.org", "2023-11-14T15:15:00-07:00"),
        message="  wip\r\n\n\ttrailing  \n\n",
    )


def test_parse_commit_reads_a_root_commit_with_an_empty_message() -> None:
    commit = worker.parse_commit(A, commit_raw(TREE, AUTHOR, COMMITTER, message=b""))

    assert (commit.parents, commit.message) == ((), "")


@pytest.mark.parametrize(
    "raw",
    [
        commit_raw(TREE, AUTHOR, COMMITTER, message=b"caf\xe9\n"),
        commit_raw(TREE, AUTHOR, COMMITTER, b"encoding ISO-8859-1", message=b"cafe\n"),
    ],
)
def test_parse_commit_refuses_non_utf8_messages(raw: bytes) -> None:
    with pytest.raises(worker.PublishError, match="non-UTF-8"):
        worker.parse_commit(A, raw)


def diff(old_mode: str, new_mode: str, old: str, new: str, status: str, path: str) -> bytes:
    return f":{old_mode} {new_mode} {old} {new} {status}\0{path}\0".encode()


def test_parse_diff_tree_reads_nul_separated_entries() -> None:
    out = diff("000000", "100644", Z, A, "A", "new file.txt") + diff(
        "100644", "000000", B, Z, "D", "dir/gone"
    )

    assert worker.parse_diff_tree(out) == [
        worker.DiffEntry("000000", "100644", Z, A, "A", "new file.txt"),
        worker.DiffEntry("100644", "000000", B, Z, "D", "dir/gone"),
    ]
    assert worker.parse_diff_tree(b"") == []


TEXT = b"line one\r\nline two\n"


@pytest.mark.parametrize(
    ("entry", "data", "expected", "upload"),
    [
        (
            worker.DiffEntry("000000", "100644", Z, A, "A", "a.txt"),
            TEXT,
            {"path": "a.txt", "mode": "100644", "type": "blob", "content": TEXT.decode()},
            None,
        ),
        (
            worker.DiffEntry("100644", "100755", B, A, "M", "run.sh"),
            b"#!/bin/sh\n",
            {"path": "run.sh", "mode": "100755", "type": "blob", "content": "#!/bin/sh\n"},
            None,
        ),
        (
            worker.DiffEntry("100644", "000000", B, Z, "D", "gone.txt"),
            None,
            {"path": "gone.txt", "mode": "100644", "type": "blob", "sha": None},
            None,
        ),
        (
            worker.DiffEntry("100644", "100755", A, A, "M", "tool"),
            None,
            {"path": "tool", "mode": "100755", "type": "blob", "sha": A},
            None,
        ),
        (
            worker.DiffEntry("000000", "120000", Z, A, "A", "link"),
            b"target/file",
            {"path": "link", "mode": "120000", "type": "blob", "content": "target/file"},
            None,
        ),
        (
            worker.DiffEntry("160000", "160000", B, A, "M", "vendor/lib"),
            None,
            {"path": "vendor/lib", "mode": "160000", "type": "commit", "sha": A},
            None,
        ),
        (
            worker.DiffEntry("160000", "000000", B, Z, "D", "vendor/lib"),
            None,
            {"path": "vendor/lib", "mode": "160000", "type": "commit", "sha": None},
            None,
        ),
        (
            worker.DiffEntry("000000", "100644", Z, A, "A", "logo.png"),
            b"\x89PNG\0\0",
            {"path": "logo.png", "mode": "100644", "type": "blob", "sha": A},
            b"\x89PNG\0\0",
        ),
        (
            worker.DiffEntry("000000", "100644", Z, A, "A", "latin1.txt"),
            b"caf\xe9",
            {"path": "latin1.txt", "mode": "100644", "type": "blob", "sha": A},
            b"caf\xe9",
        ),
    ],
)
def test_tree_entry(
    entry: worker.DiffEntry,
    data: bytes | None,
    expected: dict[str, object],
    upload: bytes | None,
) -> None:
    assert worker.tree_entry(entry, data) == (expected, upload)


def test_large_text_goes_as_a_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "INLINE_MAX_BYTES", 4)
    entry = worker.DiffEntry("000000", "100644", Z, A, "A", "big.txt")

    assert worker.tree_entry(entry, b"12345") == (
        {"path": "big.txt", "mode": "100644", "type": "blob", "sha": A},
        b"12345",
    )


@pytest.mark.parametrize(
    ("headers", "wait"),
    [
        ({"retry-after": "7"}, 7.0),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}, 30.0),
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "900"}, 0.0),
        ({"x-ratelimit-remaining": "12", "x-ratelimit-reset": "1030"}, None),
        ({}, None),
    ],
)
def test_retry_wait(headers: dict[str, str], wait: float | None) -> None:
    assert worker.retry_wait(headers, now=1000.0) == wait


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
    assert worker.classify(status, message, wait, ref_write) == action


def test_scrub_redacts_before_clipping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "MAX_GITHUB_MESSAGE_CHARS", 20)
    token = "ghs_" + "x" * 30

    assert worker.scrub(f"bad\n  token {token} here", (token, "")) == "bad token *** here"
    assert token[:8] not in worker.scrub("y" * 10 + token, (token,))


def test_api_mode_without_the_api_url_fails_setup(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GH_TOKEN", "ghs_secret")
    monkeypatch.setenv("GITHUB_SERVER_URL", SERVER)
    server.manifest = make_manifest([agent_stage("implement", "edit")], repo_url=origin.url)

    assert run_worker(server, tmp_path / "ws") == 0

    done = server.complete()
    assert done is not None
    assert done["status"] == "failed"
    assert "GITHUB_API_URL" in done["error"]
    assert not origin.has_branch(BRANCH)


def test_a_token_off_actions_still_pushes_with_git(
    server: FakeServer, origin: Origin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghs_secret")
    server.manifest = make_manifest([agent_stage("implement", "edit")], repo_url=origin.url)

    assert run_worker(server, tmp_path / "ws") == 0

    assert server.complete() == {"status": "completed", "failed_stage": None, "error": None}
    assert origin.has_branch(BRANCH)
