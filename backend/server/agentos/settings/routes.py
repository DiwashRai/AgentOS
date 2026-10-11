"""Settings routes: Space, Repos, Intake (+ the intake query preview). Admin-only, reads too."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import Admin
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.intake import service as intake
from agentos.models import (
    IntakePreview,
    IntakePreviewRequest,
    IntakeSettings,
    IntakeSettingsUpdate,
    RepoSettings,
    RepoSettingsUpdate,
    SpaceSettings,
    SpaceSettingsUpdate,
)
from agentos.settings import service

router = APIRouter(prefix="/api/settings", tags=["settings"], responses=ERROR_RESPONSES)


@router.get("/space")
def get_space(_: Admin, ctx: Ctx) -> SpaceSettings:
    return service.get_space(ctx.db)


@router.put("/space")
def put_space(body: SpaceSettingsUpdate, user: Admin, ctx: Ctx) -> SpaceSettings:
    return service.put_space(
        ctx.db, ctx.space_store, user, body, jira_auth=not ctx.settings.dev_auth
    )


@router.get("/repos")
def get_repos(_: Admin, ctx: Ctx) -> RepoSettings:
    return service.get_repos(ctx.db)


@router.put("/repos")
def put_repos(body: RepoSettingsUpdate, user: Admin, ctx: Ctx) -> RepoSettings:
    return service.put_repos(ctx.db, ctx.space_store, user, body)


@router.get("/intake")
def get_intake(_: Admin, ctx: Ctx) -> IntakeSettings:
    return service.get_intake(ctx.db)


@router.put("/intake")
def put_intake(body: IntakeSettingsUpdate, user: Admin, ctx: Ctx) -> IntakeSettings:
    return service.put_intake(ctx.db, ctx.space_store, ctx.intake_cache, user, body)


@router.post("/intake/preview")
def preview_intake(body: IntakePreviewRequest, _: Admin, ctx: Ctx) -> IntakePreview:
    """Test a JQL before saving it: live (never cached), at most 5 items, with the same filtering
    and error messages as GET /api/intake."""
    return intake.preview(ctx.db, body.jql, ctx.jira_source, ctx.space.jira_projects)
