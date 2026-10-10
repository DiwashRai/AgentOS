"""Public, unauthenticated endpoints: health and the UI's bootstrap config."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.errors import ERROR_RESPONSES
from agentos.models import AppConfig, Health

router = APIRouter(prefix="/api", tags=["meta"], responses=ERROR_RESPONSES)


@router.get("/health")
def health() -> Health:
    return Health(status="ok")


@router.get("/config")
def app_config() -> AppConfig:
    # STUB: real values come from settings + agentos.toml.
    return AppConfig(
        auth_mode="dev",
        jira_deployment=None,
        key_prefix="AOS",
        default_repo_url="https://github.com/DiwashRai/AgentOS.git",
        default_base_branch="main",
        runtimes=["claude", "copilot"],
        default_runtime="claude",
    )
