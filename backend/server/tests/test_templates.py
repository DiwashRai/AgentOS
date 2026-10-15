"""The default template: startup seed, GET and the admin PUT (SPEC §4, §6, §15). Since request
23B it holds the scaffold and the agent defaults; the pipeline comes from a workflow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import FileEntry, Template, TicketDetail
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, default_pipeline
from agentos.templates.service import seed_default

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]


def get_template(client: TestClient) -> Template:
    response = client.get("/api/template")
    assert response.status_code == 200, response.text
    return Template.model_validate(response.json())


def get_template_json(client: TestClient) -> dict[str, Any]:
    body: dict[str, Any] = client.get("/api/template").json()
    return body


def put(client: TestClient, template: Template, **changes: Any) -> Any:
    body = {
        "scaffold": [e.model_dump() for e in template.scaffold],
        "base_version": template.version,
        **changes,
    }
    return client.put("/api/template", json=body, headers=CSRF)


def test_startup_seeds_version_1(authed: TestClient) -> None:
    template = get_template(authed)
    assert template.version == 1
    assert template.scaffold == DEFAULT_SCAFFOLD
    assert "pipeline" not in get_template_json(authed)


def test_seed_is_idempotent(authed: TestClient, ctx: AppContext) -> None:
    seed_default(ctx.db)
    seed_default(ctx.db)
    with ctx.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM templates").fetchone()[0] == 1


def test_put_appends_a_version(authed: TestClient, ctx: AppContext) -> None:
    current = get_template(authed)
    notes = FileEntry(path="NOTES.md", content="Notes for {{ticket.key}}")
    response = put(authed, current, scaffold=[notes.model_dump()])
    assert response.status_code == 200, response.text
    saved = Template.model_validate(response.json())
    assert saved.version == 2
    assert saved.scaffold == [notes]
    assert get_template(authed) == saved
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT version, created_by, pipeline FROM templates ORDER BY version"
        ).fetchall()
    me = authed.get("/api/me").json()["id"]
    assert [(r["version"], r["created_by"]) for r in rows] == [(1, None), (2, me)]
    assert rows[1]["pipeline"] == rows[0]["pipeline"]  # the unused column is carried over


def test_put_with_a_pipeline_is_refused(authed: TestClient) -> None:
    """An older client still sending `pipeline`: the pipeline lives in workflows now."""
    current = get_template(authed)
    response = put(authed, current, pipeline=default_pipeline().model_dump())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_with_a_stale_base_version_is_409(authed: TestClient) -> None:
    current = get_template(authed)
    assert put(authed, current).status_code == 200
    response = put(authed, current)  # still base_version 1
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_version"


def test_put_checks_the_scaffold_renders(authed: TestClient) -> None:
    current = get_template(authed)
    bad_jinja = [{"path": "x.md", "content": "{{ ticket.nope }}", "mode": "replace"}]
    response = put(authed, current, scaffold=bad_jinja)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "render_error"
    bad_path = [{"path": "../x.md", "content": "", "mode": "replace"}]
    response = put(authed, current, scaffold=bad_path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert get_template(authed).version == 1


def test_new_tickets_render_the_current_scaffold(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    before = create_ticket("Before")
    current = get_template(authed)
    scaffold = [{"path": "NOTES.md", "content": "Notes for {{ticket.key}}", "mode": "replace"}]
    assert put(authed, current, scaffold=scaffold).status_code == 200
    after = create_ticket("After")
    assert [s.id for s in after.pipeline.stages] == [s.id for s in default_pipeline().stages]
    assert "NOTES.md" not in [
        f["path"] for f in authed.get(f"/api/tickets/{before.key}/files").json()
    ]
    files = authed.get(f"/api/tickets/{after.key}/files").json()
    assert [f["path"] for f in files] == ["NOTES.md"]
    content = authed.get(f"/api/tickets/{after.key}/files/NOTES.md").json()["content"]
    assert content == f"Notes for {after.key}"
