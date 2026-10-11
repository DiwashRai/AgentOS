"""The default template: startup seed, GET and the admin PUT (SPEC §4, §6, §15)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import FileEntry, Template, TicketDetail
from agentos.pipeline.scaffold import (
    DEFAULT_SCAFFOLD,
    LEGACY_SKILL_MD_V1,
    SKILL_MD,
    SKILL_MD_PATH,
    default_pipeline,
)
from agentos.templates.service import seed_default

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]


def get_template(client: TestClient) -> Template:
    response = client.get("/api/template")
    assert response.status_code == 200, response.text
    return Template.model_validate(response.json())


def put(client: TestClient, template: Template, **changes: Any) -> Any:
    body = {
        "pipeline": template.pipeline.model_dump(),
        "scaffold": [e.model_dump() for e in template.scaffold],
        "base_version": template.version,
        **changes,
    }
    return client.put("/api/template", json=body, headers=CSRF)


def test_startup_seeds_version_1(authed: TestClient) -> None:
    template = get_template(authed)
    assert template.version == 1
    assert template.pipeline == default_pipeline()
    assert template.scaffold == DEFAULT_SCAFFOLD


def test_seed_is_idempotent(authed: TestClient, ctx: AppContext) -> None:
    seed_default(ctx.db)
    seed_default(ctx.db)
    with ctx.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM templates").fetchone()[0] == 1


def _set_v1(ctx: AppContext, scaffold: list[FileEntry], pipeline: str | None = None) -> None:
    """Rewrite the seeded version 1, as a database from before the key-only SKILL.md has it."""
    with ctx.db.tx() as conn:
        conn.execute(
            "UPDATE templates SET scaffold = ?, pipeline = coalesce(?, pipeline) WHERE version = 1",
            (json.dumps([e.model_dump() for e in scaffold]), pipeline),
        )


def _versions(ctx: AppContext) -> list[tuple[int, str, str]]:
    with ctx.db.read() as conn:
        rows = conn.execute("SELECT version, pipeline, scaffold FROM templates ORDER BY version")
        return [(r["version"], r["pipeline"], r["scaffold"]) for r in rows.fetchall()]


def test_seed_upgrades_a_legacy_default_skill_md_once(authed: TestClient, ctx: AppContext) -> None:
    seed = default_pipeline()
    pipeline = seed.model_copy(update={"stages": seed.stages[:2]})
    notes = FileEntry(path="NOTES.md", content="Notes for {{ticket.key}}")
    legacy = FileEntry(path=SKILL_MD_PATH, content=LEGACY_SKILL_MD_V1)
    _set_v1(ctx, [legacy, *DEFAULT_SCAFFOLD[1:], notes], pipeline.model_dump_json())
    seed_default(ctx.db)
    seed_default(ctx.db)  # idempotent: the current version no longer matches
    (v1, v1_pipeline, _), (v2, v2_pipeline, _) = _versions(ctx)
    assert (v1, v2) == (1, 2)
    assert v2_pipeline == v1_pipeline  # only SKILL.md changes
    template = get_template(authed)
    assert template.version == 2
    assert template.scaffold == [*DEFAULT_SCAFFOLD, notes]
    assert template.scaffold[0].content == SKILL_MD


def test_seed_leaves_a_customised_skill_md_alone(authed: TestClient, ctx: AppContext) -> None:
    custom = FileEntry(path=SKILL_MD_PATH, content=LEGACY_SKILL_MD_V1 + "Custom.\n")
    _set_v1(ctx, [custom, *DEFAULT_SCAFFOLD[1:]])
    seed_default(ctx.db)
    assert [v for v, _, _ in _versions(ctx)] == [1]
    assert get_template(authed).scaffold[0] == custom


def test_put_appends_a_version(authed: TestClient, ctx: AppContext) -> None:
    current = get_template(authed)
    pipeline = current.pipeline.model_dump()
    pipeline["stages"] = pipeline["stages"][:2]
    response = put(authed, current, pipeline=pipeline)
    assert response.status_code == 200, response.text
    saved = Template.model_validate(response.json())
    assert saved.version == 2
    assert [s.id for s in saved.pipeline.stages] == ["plan", "implement"]
    assert get_template(authed) == saved
    with ctx.db.read() as conn:
        rows = conn.execute("SELECT version, created_by FROM templates ORDER BY version").fetchall()
    me = authed.get("/api/me").json()["id"]
    assert [(r["version"], r["created_by"]) for r in rows] == [(1, None), (2, me)]


def test_put_with_a_stale_base_version_is_409(authed: TestClient) -> None:
    current = get_template(authed)
    assert put(authed, current).status_code == 200
    response = put(authed, current)  # still base_version 1
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_version"


def test_put_validates_the_pipeline(authed: TestClient) -> None:
    current = get_template(authed)
    pipeline = current.pipeline.model_dump()
    pipeline["stages"].append(pipeline["stages"][0])
    response = put(authed, current, pipeline=pipeline)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pipeline"


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


def test_new_tickets_copy_the_current_version(
    authed: TestClient, ctx: AppContext, create_ticket: CreateTicket
) -> None:
    before = create_ticket("Before")
    current = get_template(authed)
    pipeline = current.pipeline.model_dump()
    pipeline["stages"] = pipeline["stages"][1:2]
    scaffold = [{"path": "NOTES.md", "content": "Notes for {{ticket.key}}", "mode": "replace"}]
    assert put(authed, current, pipeline=pipeline, scaffold=scaffold).status_code == 200
    after = create_ticket("After")
    assert [s.id for s in before.pipeline.stages] == [s.id for s in default_pipeline().stages]
    assert [s.id for s in after.pipeline.stages] == ["implement"]
    files = authed.get(f"/api/tickets/{after.key}/files").json()
    assert [f["path"] for f in files] == ["NOTES.md"]
    content = authed.get(f"/api/tickets/{after.key}/files/NOTES.md").json()["content"]
    assert content == f"Notes for {after.key}"
    with ctx.db.read() as conn:
        rows = conn.execute(
            "SELECT t.key, tpl.version FROM tickets t JOIN templates tpl ON tpl.id = t.template_id"
        ).fetchall()
    assert {r["key"]: r["version"] for r in rows} == {before.key: 1, after.key: 2}
