"""Settings routes. Space, Repos, Intake, Runner and Field mappings are versioned documents: GET
reads, PUT saves (`{value, base_version}`), POST `.../validate` checks (`{value}`) without saving.
Plus the intake query preview, the Jira field picker and the mapping preview, and Runtimes.
Admin-only, reads too."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import Admin
from agentos.context import AppContext
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.intake import service as intake
from agentos.models import (
    IntakePreview,
    IntakePreviewRequest,
    IntakeSettings,
    IntakeSettingsUpdate,
    IntakeSettingsValidate,
    JiraField,
    MappingPreview,
    MappingPreviewRequest,
    MappingSettings,
    MappingSettingsUpdate,
    MappingSettingsValidate,
    RepoSettings,
    RepoSettingsUpdate,
    RepoSettingsValidate,
    RunnerSettings,
    RunnerSettingsUpdate,
    RunnerSettingsValidate,
    RuntimeSettings,
    RuntimeSettingsUpdate,
    SpaceSettings,
    SpaceSettingsUpdate,
    SpaceSettingsValidate,
)
from agentos.settings import service
from agentos.settings.service import INTAKE, MAPPINGS, REPOS, RUNNER, SPACE, SettingsEnv

router = APIRouter(prefix="/api/settings", tags=["settings"], responses=ERROR_RESPONSES)


def _env(ctx: AppContext) -> SettingsEnv:
    return SettingsEnv(
        jira_auth=not ctx.settings.dev_auth,
        launcher=None if ctx.settings.launcher == "local" else ctx.settings.launcher,
        token_set=ctx.settings.github_token is not None,
        intake_cache=ctx.intake_cache,
    )


# ------------------------------------------------------------------ space


@router.get("/space")
def get_space(_: Admin, ctx: Ctx) -> SpaceSettings:
    return service.read(ctx.db, SPACE, _env(ctx))


@router.put("/space")
def put_space(body: SpaceSettingsUpdate, user: Admin, ctx: Ctx) -> SpaceSettings:
    return service.save(ctx.db, SPACE, user, body.value, body.base_version, _env(ctx))


@router.post("/space/validate", status_code=204)
def validate_space(body: SpaceSettingsValidate, _: Admin, ctx: Ctx) -> None:
    service.validate(ctx.db, SPACE, body.value, _env(ctx))


# ------------------------------------------------------------------ repos


@router.get("/repos")
def get_repos(_: Admin, ctx: Ctx) -> RepoSettings:
    return service.read(ctx.db, REPOS, _env(ctx))


@router.put("/repos")
def put_repos(body: RepoSettingsUpdate, user: Admin, ctx: Ctx) -> RepoSettings:
    return service.save(ctx.db, REPOS, user, body.value, body.base_version, _env(ctx))


@router.post("/repos/validate", status_code=204)
def validate_repos(body: RepoSettingsValidate, _: Admin, ctx: Ctx) -> None:
    service.validate(ctx.db, REPOS, body.value, _env(ctx))


# ------------------------------------------------------------------ intake


@router.get("/intake")
def get_intake(_: Admin, ctx: Ctx) -> IntakeSettings:
    return service.read(ctx.db, INTAKE, _env(ctx))


@router.put("/intake")
def put_intake(body: IntakeSettingsUpdate, user: Admin, ctx: Ctx) -> IntakeSettings:
    return service.save(ctx.db, INTAKE, user, body.value, body.base_version, _env(ctx))


@router.post("/intake/validate", status_code=204)
def validate_intake(body: IntakeSettingsValidate, _: Admin, ctx: Ctx) -> None:
    service.validate(ctx.db, INTAKE, body.value, _env(ctx))


@router.post("/intake/preview")
def preview_intake(body: IntakePreviewRequest, _: Admin, ctx: Ctx) -> IntakePreview:
    """Test a JQL before saving it: live (never cached), at most 5 items, with the same filtering
    and error messages as GET /api/intake."""
    return intake.preview(ctx.db, body.jql, ctx.jira_source, ctx.space.jira_projects)


# ------------------------------------------------------------------ field mappings


@router.get("/mappings")
def get_mappings(_: Admin, ctx: Ctx) -> MappingSettings:
    return service.read(ctx.db, MAPPINGS, _env(ctx))


@router.put("/mappings")
def put_mappings(body: MappingSettingsUpdate, user: Admin, ctx: Ctx) -> MappingSettings:
    """Replace the Jira field mappings, then re-apply a `base_branch` mapping to the unlocked
    tickets it set (from the Jira value stored at import; no Jira call)."""
    return service.save(ctx.db, MAPPINGS, user, body.value, body.base_version, _env(ctx))


@router.post("/mappings/validate", status_code=204)
def validate_mappings(body: MappingSettingsValidate, _: Admin, ctx: Ctx) -> None:
    service.validate(ctx.db, MAPPINGS, body.value, _env(ctx))


@router.get("/mappings/jira-fields")
def get_jira_fields(_: Admin, ctx: Ctx) -> list[JiraField]:
    """Jira's fields (via the service account) for the mapping field picker; cached ~10 min.
    501 when Jira isn't configured, 502 `upstream_error` when it fails."""
    return service.jira_fields(ctx.jira_source, ctx.jira_fields_cache)


@router.post("/mappings/preview")
def preview_mapping(body: MappingPreviewRequest, _: Admin, ctx: Ctx) -> MappingPreview:
    """ "Try it": the unsaved mapping on typed `values`, or on an `issue`'s field (read live)."""
    return service.preview_mapping(ctx.jira_source, body)


# ------------------------------------------------------------------ runner


@router.get("/runner")
def get_runner(_: Admin, ctx: Ctx) -> RunnerSettings:
    return service.read(ctx.db, RUNNER, _env(ctx))


@router.put("/runner")
def put_runner(body: RunnerSettingsUpdate, user: Admin, ctx: Ctx) -> RunnerSettings:
    """The github launcher reads the runner at its next launch."""
    return service.save(ctx.db, RUNNER, user, body.value, body.base_version, _env(ctx))


@router.post("/runner/validate", status_code=204)
def validate_runner(body: RunnerSettingsValidate, _: Admin, ctx: Ctx) -> None:
    service.validate(ctx.db, RUNNER, body.value, _env(ctx))


# ------------------------------------------------------------------ runtimes


@router.get("/runtimes")
def get_runtimes(_: Admin, ctx: Ctx) -> RuntimeSettings:
    return service.get_runtimes(ctx.db)


@router.put("/runtimes")
def put_runtimes(body: RuntimeSettingsUpdate, user: Admin, ctx: Ctx) -> RuntimeSettings:
    return service.put_runtimes(ctx.db, user, body)
