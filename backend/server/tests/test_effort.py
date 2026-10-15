"""Effort per stage: `Stage.effort`, the Space default (`TemplateDefaults.effort`,
`AppConfig.default_effort`) and the runtimes' `effort_args`."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentos.models import AppConfig, RuntimeSettings, Stage, TemplateDefaults
from agentos.pipeline.manifest import resolve_agent
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig, RuntimeSpec
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
