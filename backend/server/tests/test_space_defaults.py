"""Space-wide agent defaults (request 17): `Template.defaults`, `AppConfig.default_runtime` /
`default_model`, and the resolution of null stage runtimes/models when a manifest is built."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import (
    AppConfig,
    FileEntry,
    Manifest,
    Template,
    TemplateDefaults,
    TicketDetail,
)
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, LEGACY_SKILL_MD_V1, SKILL_MD_PATH
from agentos.templates.service import seed_default
from tests.run_support import CSRF, RunDriver
from tests.test_runs import put_pipeline, stage_of

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Any]

FAKE_ARGV = ["python", "fake_agent.py"]  # runtimes_test.toml: default_runtime = "fake"
CLAUDE_ARGV = ["claude", "-p", "--output-format", "stream-json", "--verbose"]


def get_template(client: TestClient) -> Template:
    r = client.get("/api/template")
    assert r.status_code == 200, r.text
    return Template.model_validate(r.json())


def put_template(client: TestClient, **changes: Any) -> Any:
    current = get_template(client)
    body = {
        "pipeline": current.pipeline.model_dump(),
        "scaffold": [e.model_dump() for e in current.scaffold],
        "base_version": current.version,
        **changes,
    }
    return client.put("/api/template", json=body, headers=CSRF)


def set_defaults(client: TestClient, **defaults: Any) -> Template:
    r = put_template(client, defaults=defaults)
    assert r.status_code == 200, r.text
    return Template.model_validate(r.json())


def config(client: TestClient) -> AppConfig:
    return AppConfig.model_validate(client.get("/api/config").json())


def first_argv(manifest: Manifest) -> list[str]:
    argv = manifest.stages[0].argv
    assert argv is not None
    return argv


# ------------------------------------------------------------------ template + config


def test_no_defaults_at_first(authed: TestClient) -> None:
    assert get_template(authed).defaults == TemplateDefaults()
    c = config(authed)
    assert (c.default_runtime, c.default_model) == ("fake", None)


def test_put_sets_defaults_and_config_shows_them(authed: TestClient) -> None:
    saved = set_defaults(authed, runtime="claude", model="claude-haiku-5-5")
    assert saved.defaults == TemplateDefaults(runtime="claude", model="claude-haiku-5-5")
    assert get_template(authed) == saved
    c = config(authed)
    assert (c.default_runtime, c.default_model) == ("claude", "claude-haiku-5-5")


def test_model_only_default_keeps_the_config_runtime(authed: TestClient) -> None:
    set_defaults(authed, model="claude-haiku-5-5")
    c = config(authed)
    assert (c.default_runtime, c.default_model) == ("fake", "claude-haiku-5-5")


def test_omitted_defaults_are_carried_over_and_empty_clears(authed: TestClient) -> None:
    set_defaults(authed, model="m1")
    r = put_template(authed)  # an older client that doesn't know `defaults`
    assert r.status_code == 200, r.text
    assert Template.model_validate(r.json()).defaults == TemplateDefaults(model="m1")
    assert set_defaults(authed).defaults == TemplateDefaults()
    assert set_defaults(authed, runtime=None, model=None).defaults == TemplateDefaults()


@pytest.mark.parametrize(
    "defaults",
    [
        None,  # explicit null: omit the field to keep the current defaults
        {"runtime": "gemini"},  # not a configured runtime
        {"model": ""},
        {"model": "has space"},
        {"model": "x\n--danger"},
        {"model": "m" * 101},
        {"model": "m", "extra": 1},
    ],
)
def test_put_validates_defaults(authed: TestClient, defaults: Any) -> None:
    r = put_template(authed, defaults=defaults)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert get_template(authed).version == 1


def test_put_defaults_is_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", role="writer")
    r = put_template(client, defaults={"model": "m"})
    assert r.status_code == 403
    assert get_template(client).defaults == TemplateDefaults()


def test_seed_upgrade_carries_defaults(authed: TestClient, ctx: AppContext) -> None:

    legacy = [FileEntry(path=SKILL_MD_PATH, content=LEGACY_SKILL_MD_V1), *DEFAULT_SCAFFOLD[1:]]
    with ctx.db.tx() as conn:
        conn.execute(
            "UPDATE templates SET scaffold = ?, defaults = ? WHERE version = 1",
            (json.dumps([e.model_dump() for e in legacy]), '{"model": "m1"}'),
        )
    seed_default(ctx.db)
    template = get_template(authed)
    assert template.version == 2
    assert template.defaults == TemplateDefaults(model="m1")


# ------------------------------------------------------------------ resolution in the manifest


def test_no_default_passes_no_model(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    key = create_ticket().key
    assert first_argv(runs.start(key).manifest) == FAKE_ARGV


def test_a_default_set_after_creation_reaches_the_next_dispatch(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    key = create_ticket().key  # copied the template before the default existed
    set_defaults(authed, model="claude-haiku-5-5")
    worker = runs.start(key)
    assert all(
        s.argv == [*FAKE_ARGV, "--model", "claude-haiku-5-5"]
        for s in worker.manifest.stages
        if s.kind == "agent"
    )
    # the ticket's own pipeline still says "Space default"
    detail = authed.get(f"/api/tickets/{key}").json()
    assert all(s["model"] is None for s in detail["pipeline"]["stages"])


def test_a_stage_override_wins(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    key = create_ticket().key
    set_defaults(authed, runtime="fake", model="space-model")
    put_pipeline(authed, key, lambda p: stage_of(p, "plan").update(model="stage-model"))
    assert first_argv(runs.start(key, "plan").manifest) == [*FAKE_ARGV, "--model", "stage-model"]


def test_runtime_fallback_chain(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    """stage runtime → the Space default runtime → the config's default_runtime."""
    a, b = create_ticket("A").key, create_ticket("B").key
    put_pipeline(authed, b, lambda p: stage_of(p, "plan").update(runtime="fake"))
    set_defaults(authed, runtime="claude")
    worker = runs.start(a, "plan")
    assert first_argv(worker.manifest)[: len(CLAUDE_ARGV)] == CLAUDE_ARGV
    assert worker.complete("failed", failed_stage="plan").status_code == 204
    assert first_argv(runs.start(b, "plan").manifest) == FAKE_ARGV


def test_defaults_resolve_at_claim(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    """The manifest is built at claim, so a change between dispatch and claim applies."""
    key = create_ticket().key
    run = runs.queue(key, "plan")
    set_defaults(authed, model="late")
    assert first_argv(runs.launch_and_claim(run.id).manifest) == [*FAKE_ARGV, "--model", "late"]
