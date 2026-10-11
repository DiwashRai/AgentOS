"""Public, unauthenticated endpoints: health and the UI's bootstrap config."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import AppConfig, Health
from agentos.pipeline.runtime import space_defaults
from agentos.templates import service as templates

router = APIRouter(prefix="/api", tags=["meta"], responses=ERROR_RESPONSES)


@router.get("/health")
def health() -> Health:
    return Health(status="ok")


@router.get("/config")
def app_config(ctx: Ctx) -> AppConfig:
    settings, runtimes, space = ctx.settings, ctx.runtimes, ctx.space
    defaults = space_defaults(templates.get_defaults(ctx.db), runtimes)
    return AppConfig(
        auth_mode="dev" if settings.dev_auth else "jira",
        jira_deployment=None if settings.dev_auth else settings.jira_deployment,
        jira_url=None if settings.dev_auth else settings.jira_url,
        key_prefix=space.key_prefix,
        space_name=space.name,
        repos=[repo.info() for repo in space.repos],
        default_repo=space.default_repo,
        runtimes=sorted(runtimes.runtimes),
        default_runtime=defaults.runtime,
        default_model=defaults.model,
        server_default_runtime=runtimes.default_runtime,
        sources=["jira"] if ctx.jira_source is not None else [],
    )
