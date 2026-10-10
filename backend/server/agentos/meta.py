"""Public, unauthenticated endpoints: health and the UI's bootstrap config."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import AppConfig, Health

router = APIRouter(prefix="/api", tags=["meta"], responses=ERROR_RESPONSES)


@router.get("/health")
def health() -> Health:
    return Health(status="ok")


@router.get("/config")
def app_config(ctx: Ctx) -> AppConfig:
    settings, runtimes = ctx.settings, ctx.runtimes
    return AppConfig(
        auth_mode="dev" if settings.dev_auth else "jira",
        jira_deployment=None if settings.dev_auth else settings.jira_deployment,
        key_prefix=settings.key_prefix,
        default_repo_url=settings.default_repo_url,
        default_base_branch=settings.default_base_branch,
        runtimes=sorted(runtimes.runtimes),
        default_runtime=runtimes.default_runtime,
    )
