"""Effort per stage (request 23A): `Stage.effort`, the Space default (`TemplateDefaults.effort`,
`AppConfig.default_effort`), the runtimes' `effort_args`, and the one-time upgrade that gives
stored built-in runtimes their `effort_args`."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentos.context import AppContext
from agentos.models import AppConfig, RuntimeSettings, Stage, TemplateDefaults
from agentos.pipeline.manifest import resolve_agent
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig, RuntimeSpec
from agentos.space_store import add_builtin_effort_args, read_area, write_area
from tests.run_support import CSRF, RunDriver
from tests.test_runs import put_pipeline, stage_of
from tests.test_space_defaults import FAKE_ARGV, first_argv, set_defaults

CLAUDE = ["claude", "-p"]
RUNTIMES = RuntimesConfig(
    default_runtime="claude",
    runtimes={
        "claude": RuntimeSpec(
            argv=CLAUDE,
            model_args=["--model", "{model}"],
            effort_args=["--effort", "{effort}"],
            agent_args=["--agent", "{agent}"],
        ),
        "plain": RuntimeSpec(argv=["plain"], prompt_via="arg", prompt_args=["{prompt}"]),
    },
)
NONE = TemplateDefaults()


def agent(**fields: Any) -> Stage:
    return Stage(id="build", name="Build", kind="agent", phase="build", **fields)


# ------------------------------------------------------------------ resolution, pure


@pytest.mark.parametrize(
    ("stage", "defaults", "argv"),
    [
        (agent(), NONE, CLAUDE),
        (agent(effort="high"), NONE, [*CLAUDE, "--effort", "high"]),
        # argv, model_args, effort_args, agent_args
        (
            agent(model="opus", effort="low", agent="rev"),
            NONE,
            [*CLAUDE, "--model", "opus", "--effort", "low", "--agent", "rev"],
        ),
        # a null effort takes the Space default; the stage's own wins
        (agent(), TemplateDefaults(effort="medium"), [*CLAUDE, "--effort", "medium"]),
        (agent(effort="max"), TemplateDefaults(effort="low"), [*CLAUDE, "--effort", "max"]),
        # the Space effort belongs to the Space runtime, like the model
        (agent(runtime="plain"), TemplateDefaults(effort="low"), ["plain", "P"]),
        # a runtime without effort_args passes no flag, even for the stage's own effort
        (agent(runtime="plain", effort="high"), NONE, ["plain", "P"]),
    ],
)
def test_resolve_agent_effort(stage: Stage, defaults: TemplateDefaults, argv: list[str]) -> None:
    assert resolve_agent(stage, "P", RUNTIMES, defaults=defaults)[0] == argv


@pytest.mark.parametrize("effort", ["", "has space", "x\n--danger", "e" * 33, "a/b"])
def test_effort_is_one_safe_token(effort: str) -> None:
    with pytest.raises(ValidationError):
        agent(effort=effort)
    with pytest.raises(ValidationError):
        TemplateDefaults(effort=effort)


def test_built_in_runtimes_have_an_effort_flag() -> None:
    assert {r.name: r.effort_args for r in BUILTIN_RUNTIMES} == {
        "claude": ["--effort", "{effort}"],
        "copilot": ["--effort", "{effort}"],
    }


# ------------------------------------------------------------------ API


def config(client: TestClient) -> AppConfig:
    return AppConfig.model_validate(client.get("/api/config").json())


def runtimes(client: TestClient) -> RuntimeSettings:
    return RuntimeSettings.model_validate(client.get("/api/settings/runtimes").json())


def give_fake_an_effort_flag(client: TestClient) -> None:
    current = runtimes(client)
    items = [r.model_dump() for r in current.runtimes]
    items[0]["effort_args"] = ["--effort", "{effort}"]  # "fake", the server default
    body = {"runtimes": items, "base_version": current.version}
    r = client.put("/api/settings/runtimes", json=body, headers=CSRF)
    assert r.status_code == 200, r.text


def test_space_default_effort_in_template_and_config(authed: TestClient) -> None:
    assert config(authed).default_effort is None
    saved = set_defaults(authed, effort="high")
    assert saved.defaults == TemplateDefaults(effort="high")
    assert config(authed).default_effort == "high"


def test_runtimes_round_trip_effort_args_and_omitted_is_none(authed: TestClient) -> None:
    give_fake_an_effort_flag(authed)
    got = runtimes(authed).runtimes
    assert got[0].effort_args == ["--effort", "{effort}"]
    assert got[1].effort_args == []  # the test Space's claude has none


def test_a_stage_effort_reaches_the_manifest(
    authed: TestClient, create_ticket: Any, runs: RunDriver
) -> None:
    key = create_ticket().key
    put_pipeline(authed, key, lambda p: stage_of(p, "plan").update(effort="high"))
    detail = authed.get(f"/api/tickets/{key}").json()
    assert next(s for s in detail["pipeline"]["stages"] if s["id"] == "plan")["effort"] == "high"
    # the runtime has no effort flag yet: no error, no flag
    worker = runs.start(key, "plan")
    assert first_argv(worker.manifest) == FAKE_ARGV
    assert worker.complete("failed", failed_stage="plan").status_code == 204
    give_fake_an_effort_flag(authed)
    set_defaults(authed, effort="low")  # the stage's own effort wins
    assert first_argv(runs.start(key, "plan").manifest) == [*FAKE_ARGV, "--effort", "high"]


def test_the_space_default_effort_reaches_a_stage_without_one(
    authed: TestClient, create_ticket: Any, runs: RunDriver
) -> None:
    key = create_ticket().key
    give_fake_an_effort_flag(authed)
    set_defaults(authed, model="m", effort="low")
    assert first_argv(runs.start(key, "plan").manifest) == [
        *FAKE_ARGV,
        "--model",
        "m",
        "--effort",
        "low",
    ]


# ------------------------------------------------------------------ the stored built-ins upgrade


def stored_runtimes(ctx: AppContext) -> tuple[list[dict[str, Any]], int]:
    with ctx.db.read() as conn:
        row = read_area(conn, "runtimes")
    assert row is not None
    return row.value["runtimes"], row.version


def test_stored_built_ins_gain_effort_args_once(authed: TestClient, ctx: AppContext) -> None:
    claude, copilot = (r.model_dump(mode="json") for r in BUILTIN_RUNTIMES)
    edited = copilot | {"argv": ["copilot", "--mine"]}  # an admin changed it: left alone
    custom = {"name": "fake", "argv": FAKE_ARGV, "model_args": [], "agent_args": []}
    legacy = [{k: v for k, v in r.items() if k != "effort_args"} for r in (claude, edited, custom)]
    with ctx.db.tx() as conn:
        write_area(conn, "runtimes", {"runtimes": legacy}, version=5, actor_id=None)

    assert add_builtin_effort_args(ctx.db) == ["claude"]
    stored, version = stored_runtimes(ctx)
    assert version == 6
    assert stored[0]["effort_args"] == ["--effort", "{effort}"]
    assert "effort_args" not in stored[1]
    assert "effort_args" not in stored[2]

    assert add_builtin_effort_args(ctx.db) == []  # at most once
    assert stored_runtimes(ctx)[1] == 6
