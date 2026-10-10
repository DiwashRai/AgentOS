"""Versioned bundle files (SPEC §6): human routes and the run-service primitives."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.errors import ApiError
from agentos.files import service
from agentos.models import FileContent, FileEntry, FileInfo, FileVersionInfo, TicketDetail
from agentos.validate import MAX_FILE_BYTES

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]


def code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value


def put(
    client: TestClient, key: str, path: str, content: str, base: int, mode: str = "replace"
) -> Any:
    return client.put(
        f"/api/tickets/{key}/files/{path}",
        json={"content": content, "mode": mode, "base_version": base},
        headers=CSRF,
    )


def delete(client: TestClient, key: str, path: str, base: int) -> Any:
    return client.delete(f"/api/tickets/{key}/files/{path}?base_version={base}", headers=CSRF)


def get(client: TestClient, key: str, path: str, version: int | None = None) -> Any:
    query = "" if version is None else f"?version={version}"
    return client.get(f"/api/tickets/{key}/files/{path}{query}")


def versions(client: TestClient, key: str, path: str) -> list[FileVersionInfo]:
    response = client.get(f"/api/tickets/{key}/versions/{path}")
    assert response.status_code == 200, response.text
    return [FileVersionInfo.model_validate(v) for v in response.json()]


def paths(client: TestClient, key: str) -> list[str]:
    return [f["path"] for f in client.get(f"/api/tickets/{key}/files").json()]


def detail(client: TestClient, key: str) -> TicketDetail:
    return TicketDetail.model_validate(client.get(f"/api/tickets/{key}").json())


def ticket_id(ctx: AppContext, key: str) -> int:
    with ctx.db.read() as conn:
        value: int = conn.execute("SELECT id FROM tickets WHERE key = ?", (key,)).fetchone()["id"]
    return value


@pytest.fixture
def ticket(authed: TestClient, create_ticket: CreateTicket) -> TicketDetail:
    return create_ticket("Files")


def tasks_path(t: TicketDetail) -> str:
    return f".claude/skills/{t.skill_name}/TASKS.md"


# ------------------------------------------------------------------ list / get


def test_list_and_get(authed: TestClient, ticket: TicketDetail) -> None:
    infos = [
        FileInfo.model_validate(f) for f in authed.get(f"/api/tickets/{ticket.key}/files").json()
    ]
    assert [i.path for i in infos] == sorted(i.path for i in infos)  # ordered by path
    claude = next(i for i in infos if i.path == "CLAUDE.md")
    assert (claude.version, claude.size, claude.mode) == (1, len(b"@AGENTS.md"), "block")
    f = FileContent.model_validate(get(authed, ticket.key, "CLAUDE.md").json())
    assert f.content == "@AGENTS.md"
    assert f.model_dump(exclude={"content"}) == claude.model_dump()
    assert get(authed, ticket.key, "CLAUDE.md", 1).json()["content"] == "@AGENTS.md"


@pytest.mark.parametrize(
    ("path", "version", "expected"),
    [
        ("NOPE.md", None, "file_not_found"),
        ("NOPE.md", 1, "file_not_found"),
        ("CLAUDE.md", 2, "version_not_found"),
        ("claude.md", None, "file_not_found"),  # lookups are exact
    ],
)
def test_get_missing(
    authed: TestClient, ticket: TicketDetail, path: str, version: int | None, expected: str
) -> None:
    response = get(authed, ticket.key, path, version)
    assert response.status_code == 404
    assert code(response) == expected


def test_unknown_ticket_is_404(authed: TestClient) -> None:
    for response in (
        authed.get("/api/tickets/AOS-9/files"),
        get(authed, "AOS-9", "CLAUDE.md"),
        authed.get("/api/tickets/AOS-9/versions/CLAUDE.md"),
        put(authed, "AOS-9", "x.md", "x", 0),
        delete(authed, "AOS-9", "x.md", 1),
    ):
        assert response.status_code == 404
        assert code(response) == "ticket_not_found"


# ------------------------------------------------------------------ write


def test_create_update_and_stale(authed: TestClient, ticket: TicketDetail) -> None:
    created = put(authed, ticket.key, "docs/notes.md", "one", 0)
    assert created.status_code == 200, created.text
    info = FileInfo.model_validate(created.json())
    assert (info.path, info.version, info.size, info.mode) == ("docs/notes.md", 1, 3, "replace")
    assert info.author is not None
    assert info.author.display_name == "dev"
    assert info.author_run_id is None

    exists = put(authed, ticket.key, "docs/notes.md", "again", 0)
    assert exists.status_code == 409
    assert code(exists) == "stale_version"

    updated = put(authed, ticket.key, "docs/notes.md", "two ✓", 1)
    assert updated.json()["version"] == 2
    assert updated.json()["size"] == len("two ✓".encode())
    stale = put(authed, ticket.key, "docs/notes.md", "three", 1)
    assert stale.status_code == 409
    assert code(stale) == "stale_version"
    assert get(authed, ticket.key, "docs/notes.md").json()["content"] == "two ✓"
    assert get(authed, ticket.key, "docs/notes.md", 1).json()["content"] == "one"
    assert [v.version for v in versions(authed, ticket.key, "docs/notes.md")] == [1, 2]


def test_unchanged_write_is_idempotent(authed: TestClient, ticket: TicketDetail) -> None:
    before = detail(authed, ticket.key).updated_at
    same = put(authed, ticket.key, "CLAUDE.md", "@AGENTS.md", 1, mode="block")
    assert same.status_code == 200
    assert same.json()["version"] == 1
    assert len(versions(authed, ticket.key, "CLAUDE.md")) == 1
    assert detail(authed, ticket.key).updated_at == before
    # A mode change alone is a change.
    assert put(authed, ticket.key, "CLAUDE.md", "@AGENTS.md", 1).json()["version"] == 2


def test_writes_bump_updated_at(authed: TestClient, ticket: TicketDetail) -> None:
    put(authed, ticket.key, "a.md", "a", 0)
    after = detail(authed, ticket.key)
    assert after.updated_at > ticket.updated_at
    assert after.attention == "up_next"  # board fields still derive from runs, not files


def test_delete(authed: TestClient, ticket: TicketDetail) -> None:
    assert put(authed, ticket.key, "gone.md", "bye", 0).status_code == 200
    stale = delete(authed, ticket.key, "gone.md", 2)
    assert stale.status_code == 409
    assert code(stale) == "stale_version"

    assert delete(authed, ticket.key, "gone.md", 1).status_code == 204
    assert "gone.md" not in paths(authed, ticket.key)
    assert code(get(authed, ticket.key, "gone.md")) == "file_not_found"
    assert get(authed, ticket.key, "gone.md", 1).json()["content"] == "bye"
    assert code(get(authed, ticket.key, "gone.md", 2)) == "version_not_found"
    history = versions(authed, ticket.key, "gone.md")
    assert [(v.version, v.deleted, v.size) for v in history] == [(1, False, 3), (2, True, 0)]

    again = delete(authed, ticket.key, "gone.md", 2)
    assert again.status_code == 404
    assert code(again) == "file_not_found"
    assert code(delete(authed, ticket.key, "never.md", 1)) == "file_not_found"
    # Re-creating a deleted file starts from base_version 0 and continues the numbering.
    assert put(authed, ticket.key, "gone.md", "back", 2).status_code == 409
    assert put(authed, ticket.key, "gone.md", "back", 0).json()["version"] == 3


def test_bundle_locks_only_while_a_run_is_running(
    authed: TestClient, ticket: TicketDetail, add_run: AddRun, ctx: AppContext
) -> None:
    run_id = add_run(ticket.key, "queued", [("plan", "pending")])
    assert put(authed, ticket.key, "q.md", "queued is fine", 0).status_code == 200
    with ctx.db.tx() as conn:
        conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
    for response in (
        put(authed, ticket.key, "q.md", "locked", 1),
        put(authed, ticket.key, "new.md", "locked", 0),
        delete(authed, ticket.key, "q.md", 1),
    ):
        assert response.status_code == 409
        assert code(response) == "bundle_locked"
    with ctx.db.tx() as conn:
        conn.execute("UPDATE runs SET status = 'gated' WHERE id = ?", (run_id,))
    assert put(authed, ticket.key, "q.md", "unlocked", 1).status_code == 200


def test_tasks_md_counts_tasks(authed: TestClient, ticket: TicketDetail) -> None:
    path = tasks_path(ticket)
    plan = "# Tasks\n- [x] one\n- [X] two\n- [ ] three\n  * [ ] four\n- [] not a task\n"
    assert put(authed, ticket.key, path, plan, 1).status_code == 200
    t = detail(authed, ticket.key)
    assert t.tasks is not None
    assert (t.tasks.done, t.tasks.total) == (2, 4)
    summary = authed.get("/api/tickets").json()[0]
    assert summary["tasks"] == {"done": 2, "total": 4}
    assert put(authed, ticket.key, path, "# Tasks\n", 2).status_code == 200
    assert detail(authed, ticket.key).tasks is None  # no items → no progress
    put(authed, ticket.key, path, "- [x] a\n", 3)
    assert delete(authed, ticket.key, path, 4).status_code == 204
    assert detail(authed, ticket.key).tasks is None


def test_other_tasks_md_files_do_not_count(authed: TestClient, ticket: TicketDetail) -> None:
    assert put(authed, ticket.key, "docs/TASKS.md", "- [x] a\n", 0).status_code == 200
    assert detail(authed, ticket.key).tasks is None


# ------------------------------------------------------------------ validation and limits


@pytest.mark.parametrize(
    "path",
    ["..%2Fx.md", "a//b.md", ".git/config", "CON.txt", "trailing.", "ünï.md", "a\\b.md"],
)
def test_invalid_paths(authed: TestClient, ticket: TicketDetail, path: str) -> None:
    response = put(authed, ticket.key, path, "x", 0)
    assert response.status_code == 422, response.text
    assert code(response) == "validation_error"


def test_paths_are_unique_ignoring_case(authed: TestClient, ticket: TicketDetail) -> None:
    response = put(authed, ticket.key, "agents.md", "x", 0)
    assert response.status_code == 422
    assert code(response) == "validation_error"
    assert "collides" in response.json()["error"]["message"]
    # Once the other casing is deleted, the name is free.
    assert delete(authed, ticket.key, "AGENTS.md", 1).status_code == 204
    assert put(authed, ticket.key, "agents.md", "x", 0).status_code == 200


def test_block_content_must_not_contain_markers(authed: TestClient, ticket: TicketDetail) -> None:
    body = "a\n<!-- agentos:end AOS-1 -->\n"
    response = put(authed, ticket.key, "B.md", body, 0, mode="block")
    assert response.status_code == 422
    assert code(response) == "validation_error"
    assert put(authed, ticket.key, "B.md", body, 0).status_code == 200  # fine in replace mode


def test_file_size_limit(authed: TestClient, ticket: TicketDetail) -> None:
    assert put(authed, ticket.key, "big.md", "x" * MAX_FILE_BYTES, 0).status_code == 200
    response = put(authed, ticket.key, "big2.md", "é" * (MAX_FILE_BYTES // 2 + 1), 0)
    assert response.status_code == 413
    assert code(response) == "too_large"


def test_bundle_size_limit(
    authed: TestClient, ticket: TicketDetail, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = sum(f["size"] for f in authed.get(f"/api/tickets/{ticket.key}/files").json())
    monkeypatch.setattr(service, "MAX_BUNDLE_BYTES", current + 10)
    assert put(authed, ticket.key, "a.md", "x" * 10, 0).status_code == 200
    response = put(authed, ticket.key, "b.md", "x", 0)
    assert response.status_code == 413
    assert code(response) == "too_large"
    # Replacing a file counts its new size, not old + new.
    assert put(authed, ticket.key, "a.md", "y" * 10, 1).status_code == 200


# ------------------------------------------------------------------ primitives for the run service


def test_write_version_and_current_bundle(
    authed: TestClient, ticket: TicketDetail, ctx: AppContext, add_run: AddRun
) -> None:
    tid = ticket_id(ctx, ticket.key)
    run_id = add_run(ticket.key, "running", [("implement", "running")])
    with ctx.db.tx() as conn:
        assert (
            service.write_version(
                conn,
                tid,
                "out/report.md",
                "r1",
                "replace",
                author_run_id=run_id,
                author_stage="implement",
            )
            == 1
        )
        # Idempotent, no lock check (the run is running), deletes of absent files append nothing.
        assert service.write_version(conn, tid, "out/report.md", "r1", "replace") == 1
        assert service.write_version(conn, tid, "never.md", None, "replace") is None
        assert service.write_version(conn, tid, "CLAUDE.md", None, "block") == 2
        assert service.write_version(conn, tid, "CLAUDE.md", None, "replace") == 2
        bundle = service.current_bundle(conn, tid)
    assert [e.path for e in bundle] == sorted(e.path for e in bundle)
    by_path = {e.path: e for e in bundle}
    assert "CLAUDE.md" not in by_path
    assert by_path["out/report.md"] == FileEntry(path="out/report.md", content="r1")
    assert by_path["AGENTS.md"].mode == "block"
    info = FileContent.model_validate(get(authed, ticket.key, "out/report.md").json())
    assert (info.author, info.author_run_id) == (None, run_id)
    with ctx.db.read() as conn:
        stage = conn.execute(
            "SELECT author_stage FROM file_versions WHERE path = 'out/report.md'"
        ).fetchone()
    assert stage["author_stage"] == "implement"


def test_write_version_validates(authed: TestClient, ticket: TicketDetail, ctx: AppContext) -> None:
    tid = ticket_id(ctx, ticket.key)
    with ctx.db.tx() as conn:
        for path, content in (
            ("../x", "x"),
            ("agents.md", "x"),
            ("x.md", "y" * (MAX_FILE_BYTES + 1)),
        ):
            with pytest.raises(ApiError):
                service.write_version(conn, tid, path, content, "replace")
