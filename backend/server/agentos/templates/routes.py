"""Default template routes."""

from __future__ import annotations

from fastapi import APIRouter

from agentos import stubs
from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES, ApiError
from agentos.models import Template, TemplateUpdate

router = APIRouter(prefix="/api/template", tags=["template"], responses=ERROR_RESPONSES)


@router.get("")
def get_template(_: CurrentUser) -> Template:
    return stubs.STORE.template


@router.put("")
def put_template(body: TemplateUpdate, _: CurrentUser) -> Template:
    current = stubs.STORE.template
    if body.base_version != current.version:
        raise ApiError(409, "stale_version", "Template was changed by someone else")
    stubs.STORE.template = Template(
        pipeline=body.pipeline, scaffold=body.scaffold, version=current.version + 1
    )
    return stubs.STORE.template
